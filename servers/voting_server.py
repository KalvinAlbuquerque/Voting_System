# servers/voting_server.py
import Pyro4
import Pyro4.util
import json
import os
import sys
import threading
import time
import logging

# Adiciona o diretório 'common' ao sys.path para importação
current_dir = os.path.dirname(os.path.abspath(__file__))
project_root = os.path.join(current_dir, '..')
sys.path.append(project_root)

from common.constants import VOTING_SERVER_NAME_PREFIX, NAME_SERVER_HOST, NAME_SERVER_PORT, LOG_FORMAT, LOG_LEVEL

# Configura o logging para o servidor
logging.basicConfig(level=LOG_LEVEL, format=LOG_FORMAT)
logger = logging.getLogger(__name__)

# Configuração para imprimir rastreamentos de exceção remotos
sys.excepthook = Pyro4.util.excepthook

@Pyro4.expose
@Pyro4.behavior(instance_mode="single")
class VotingServer:
    """
        Classe que representa o servidor de votação

        Atributos:
        - server_id: id único do servidor.
        - votes: Dicionário para armazenar os votos de cada candidato (ex: {'Candidato A': 5}).
        - total_servers: Total de servidores ativos (nós) - atualmente não usado para controle dinâmico, mas para contexto.
        - other_servers_uris: Dicionário que mapeia server_id para URI de outros servidores Pyro4.
        - lock: Um lock de threading para proteger o estado interno durante operações concorrentes.
    """
    def __init__(self, server_id, total_servers=1):
        self.server_id = server_id
        self.votes = {}
        self.total_servers = total_servers
        self.other_servers_uris = {}
        self.lock = threading.Lock()
        logger.info(f"[{self.server_id}] Servidor de Votação inicializado.")

    def _save_votes(self):
        """Salva os votos em um arquivo JSON específico do servidor ('data/votes_server_id.json')."""
        votes_file = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'data', f'votes_{self.server_id}.json')
        with open(votes_file, 'w') as f:
            json.dump(self.votes, f, indent=4)
        logger.debug(f"[{self.server_id}] Votos salvos em '{votes_file}'.")

    def _load_state(self):
        """
        Carrega o estado (votos) de arquivos locais persistidos.
        Esta é a base antes da sincronização com outros nós.
        """
        votes_file = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'data', f'votes_{self.server_id}.json')

        with self.lock: # Proteger o acesso ao estado durante o carregamento/sincronização
            if os.path.exists(votes_file):
                with open(votes_file, 'r') as f:
                    self.votes = json.load(f)
            else:
                self.votes = {}
            logger.info(f"[{self.server_id}] Estado carregado localmente: Votos: {self.votes}")

    def _sync_with_other_servers(self):
        """
        Tenta sincronizar o estado com outro servidor ativo ao iniciar.
        Ele vai pedir o estado completo de um servidor e substituir o seu próprio.
        """
        logger.info(f"[{self.server_id}] Tentando sincronizar estado com outros servidores...")
        self.discover_other_servers() # Re-descobre os servidores

        if not self.other_servers_uris:
            logger.info(f"[{self.server_id}] Nenhuma outra réplica ativa para sincronizar. Iniciando com estado local.")
            return

        for other_server_id, uri in self.other_servers_uris.items():
            try:
                # Usando o proxy como context manager para garantir liberação de recursos
                with Pyro4.Proxy(uri) as other_server_proxy:
                    logger.info(f"[{self.server_id}] Solicitando estado completo de '{other_server_id}' em {uri}...")

                    # Chamada remota para obter o estado completo
                    full_state = other_server_proxy.get_full_state()

                    with self.lock:
                        self.votes = full_state['votes']
                        self._save_votes() # Persiste o estado sincronizado
                    logger.info(f"[{self.server_id}] Sincronização completa com '{other_server_id}'. Estado atualizado.")
                    logger.info(f"[{self.server_id}] Novo estado: Votos: {self.votes}")
                    return # Sincronizado com sucesso com um servidor, podemos parar.
            except (Pyro4.errors.CommunicationError, Pyro4.errors.NamingError, Pyro4.errors.TimeoutError) as e:
                logger.warning(f"[{self.server_id}] Erro ao sincronizar com '{other_server_id}' ({uri}): {e}. Tentando o próximo...")
            except Exception as e:
                logger.error(f"[{self.server_id}] Erro inesperado ao sincronizar com '{other_server_id}' ({uri}): {e}")

        logger.warning(f"[{self.server_id}] Não foi possível sincronizar com nenhum outro servidor ativo. Iniciando com estado local (potencialmente desatualizado).")


    def discover_other_servers(self):
        """
        Descobre outros servidores de votação registrados no Name Server e armazena seus URIs.
        Isso é importante para a replicação e sincronização.
        """
        try:
            ns = Pyro4.locateNS(host=NAME_SERVER_HOST, port=NAME_SERVER_PORT)
            all_servers = ns.list(prefix=VOTING_SERVER_NAME_PREFIX)
            new_other_servers_uris = {}
            for name, uri in all_servers.items():
                current_id = name.replace(VOTING_SERVER_NAME_PREFIX, '')
                if current_id != self.server_id:
                    new_other_servers_uris[current_id] = uri
            self.other_servers_uris = new_other_servers_uris
            logger.debug(f"[{self.server_id}] Outros servidores descobertos para sincronização: {self.other_servers_uris}")
        except Pyro4.errors.NamingError as e:
            logger.error(f"[{self.server_id}] Erro ao localizar o Name Server: {e}. Verifique se o Name Server está rodando.")
            self.other_servers_uris = {}

    @Pyro4.expose
    def cast_vote(self, candidate):
        """
        Recebe um voto para um candidato específico.
        Realiza a replicação do voto.
        Em caso de falha na replicação, reverte o voto localmente.
        Retorna uma tupla (sucesso: bool, mensagem: str).
        """
        logger.info(f"[{self.server_id}] Tentativa de voto para candidato='{candidate}'")

        with self.lock: # Proteger o estado durante a validação e atualização local
            # Atualiza o estado localmente antes da replicação
            self.votes[candidate] = self.votes.get(candidate, 0) + 1
            logger.info(f"[{self.server_id}] Voto para '{candidate}' registrado localmente.")

        # Tenta replicar o voto para outros servidores
        success, message = self._replicate_vote(candidate)

        with self.lock: # Proteger o estado ao persistir ou reverter
            if success:
                self._save_votes() # Persiste o estado local
                logger.info(f"[{self.server_id}] Voto para '{candidate}' processado e replicado com sucesso.")
                return True, "Voto registrado com sucesso!"
            else:
                # Se a replicação falhar, reverte o voto local
                logger.error(f"[{self.server_id}] Erro: Falha na replicação do voto. Revertendo estado local.")
                self.votes[candidate] -= 1
                if self.votes[candidate] == 0:
                    del self.votes[candidate]
                self._save_votes() # Persiste a reversão
                return False, message


    def _replicate_vote(self, candidate):
        """
        Tenta replicar o voto para outros servidores.
        Usa uma estratégia de "quórum" simples: se a maioria dos outros servidores (incluindo o próprio nó)
        confirmar a atualização, considera-se a replicação bem-sucedida.
        """
        logger.info(f"[{self.server_id}] Iniciando replicação para outros servidores...")
        self.discover_other_servers() # Re-descobre os servidores para pegar novos ou remover caídos

        successful_replications = 0
        total_replicas_to_contact = len(self.other_servers_uris)

        if total_replicas_to_contact == 0:
            logger.info(f"[{self.server_id}] Nenhuma outra réplica para sincronizar. Voto localmente consistente.")
            return True, "Voto processado localmente."

        # Para cada outro servidor, tente enviar o voto para que ele atualize seu estado.
        for other_server_id, uri in self.other_servers_uris.items():
            if other_server_id == self.server_id: # Evita tentar replicar para si mesmo
                continue
            try:
                time.sleep(3.0) # Atraso de 0.5 segundos antes de tentar replicar
                # Usando o proxy como context manager
                with Pyro4.Proxy(uri) as other_server_proxy:
                    # Chamamos um método interno para que o outro servidor atualize o estado
                    logger.info(f"[{self.server_id}] Replicando voto para '{other_server_id}' em {uri}...")
                    is_ok, msg = other_server_proxy.internal_update_state(candidate)
                    if is_ok:
                        successful_replications += 1
                        logger.info(f"[{self.server_id}] Replicação bem-sucedida para '{other_server_id}'.")
                    else:
                        logger.warning(f"[{self.server_id}] Replicação falhou para '{other_server_id}': {msg}")
            except (Pyro4.errors.CommunicationError, Pyro4.errors.NamingError, Pyro4.errors.TimeoutError) as e:
                logger.error(f"[{self.server_id}] Erro de comunicação com '{other_server_id}' ({uri}): {e}")
            except Exception as e:
                logger.error(f"[{self.server_id}] Erro inesperado ao replicar para '{other_server_id}' ({uri}): {e}")

        # Condição de sucesso para replicação (exemplo: quórum de maioria)
        # +1 para contar o próprio servidor que já processou o voto localmente
        required_successes = (total_replicas_to_contact + 1) // 2
        
        # Garante que required_successes seja no mínimo 1 se houver apenas o servidor local
        if total_replicas_to_contact == 0:
             required_successes = 1 # Se sou o único servidor, o sucesso local já é suficiente

        if (successful_replications + 1) >= required_successes:
            logger.info(f"[{self.server_id}] Replicação concluída. Sucesso na maioria ({successful_replications+1}/{total_replicas_to_contact+1} nós).")
            return True, "Voto replicado e processado com sucesso."
        else:
            logger.error(f"[{self.server_id}] Falha na replicação. Apenas {successful_replications+1} nós atualizaram o estado. Quórum ({required_successes}) não atingido.")
            return False, "Falha na replicação do voto para a maioria dos nós."


    @Pyro4.expose
    def internal_update_state(self, candidate):
        """
        Método interno para ser chamado por outros servidores para sincronização.
        Recebe um voto já validado por outro nó e apenas atualiza o estado local.
        Retorna uma tupla (sucesso: bool, mensagem: str).
        """
        logger.info(f"[{self.server_id}] Recebido pedido de atualização de estado (voto) para candidato='{candidate}'.")
        with self.lock:
            self.votes[candidate] = self.votes.get(candidate, 0) + 1
            self._save_votes()
            logger.info(f"[{self.server_id}] Estado local atualizado por replicação para '{candidate}'.")
            return True, "Estado atualizado com sucesso."

    @Pyro4.expose
    def get_results(self):
        """Retorna os resultados atuais da votação."""
        logger.info(f"[{self.server_id}] Requisição de resultados recebida.")
        with self.lock:
            return dict(self.votes) # Retorna uma cópia para evitar modificações externas

    @Pyro4.expose
    def get_full_state(self):
        """
        Retorna o estado completo do servidor (votos).
        Usado para sincronização de nós que retornam à rede.
        """
        logger.info(f"[{self.server_id}] Requisição de estado completo recebida.")
        with self.lock:
            return {
                'votes': dict(self.votes),
            }

    def run(self):
        """Inicia o daemon Pyro e registra o servidor."""
        # Carrega o estado inicial ao iniciar
        self._load_state()

        daemon = Pyro4.Daemon(host=NAME_SERVER_HOST)
        try:
            ns = Pyro4.locateNS(host=NAME_SERVER_HOST, port=NAME_SERVER_PORT)
        except Pyro4.errors.NamingError as e:
            logger.critical(f"[{self.server_id}] Falha ao conectar ao Name Server em {NAME_SERVER_HOST}:{NAME_SERVER_PORT}. Por favor, inicie o Name Server primeiro. Erro: {e}")
            sys.exit(1)


        # Registra o servidor com um nome único
        uri = daemon.register(self, VOTING_SERVER_NAME_PREFIX + self.server_id)
        ns.register(VOTING_SERVER_NAME_PREFIX + self.server_id, uri)

        logger.info(f"[{self.server_id}] Servidor de Votação registrado com nome: '{VOTING_SERVER_NAME_PREFIX + self.server_id}'")
        logger.info(f"[{self.server_id}] URI: {uri}")
        logger.info(f"[{self.server_id}] Daemon Pyro pronto para escutar requisições...")

        # Sincroniza com outros servidores após o registro
        self._sync_with_other_servers()

        daemon.requestLoop() # Inicia o loop de eventos do Pyro

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Uso: python voting_server.py <server_id>")
        print("Ex: python voting_server.py server1")
        print("Ex: python voting_server.py server2")
        sys.exit(1)

    server_id = sys.argv[1]
    server = VotingServer(server_id)
    server.run()