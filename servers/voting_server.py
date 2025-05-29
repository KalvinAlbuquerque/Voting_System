import Pyro4
import Pyro4.util
import json
import os
import sys
import threading
import time

# Adiciona o diretório 'common' ao sys.path para importação
current_dir = os.path.dirname(os.path.abspath(__file__))
project_root = os.path.join(current_dir, '..')
sys.path.append(project_root)

from common.constants import VOTING_SERVER_NAME_PREFIX, NAME_SERVER_HOST, NAME_SERVER_PORT

# Configuração para imprimir rastreamentos de exceção remotos
sys.excepthook = Pyro4.util.excepthook

@Pyro4.expose
@Pyro4.behavior(instance_mode="single")
class VotingServer:
    """ 
        Classe que representa o servidor de votação
        
        Atributos:
        -Id: id do servidor
        -Voters: os eleitores cadastrados. Dicionário contendo o usuário e senha dos eleitores cadastrados no JSON
        -Votes: dicionário para armazenar os votos de cada candidato
        -voted_users: um conjunto (set) que armazena o nome dos eleitores que já votaram
        -total_servers: total de servidores ativos(nós) 
        -lock: para manter a sincronização
    """
    def __init__(self, server_id, total_servers=1):
        self.server_id = server_id
        self.voters = self._load_voters() 
        self.votes = {} 
        self.voted_users = set() # Eleitores que já votaram
        self.total_servers = total_servers # Total de servidores ativos para comunicação
        self.other_servers_uris = {} # Mapeia server_id para URI de outros servidores
        self.lock = threading.Lock() # Um lock para proteger o estado interno durante operações
        print(f"[{self.server_id}] Servidor de Votação inicializado.")
        print(f"[{self.server_id}] Eleitores carregados: {len(self.voters)}")

    def _load_voters(self):
        """Carrega eleitores de um arquivo JSON simulado."""
        voters_file = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'data', 'voters.json')
        if os.path.exists(voters_file):
            with open(voters_file, 'r') as f:
                return json.load(f)
        print(f"[{self.server_id}] Aviso: Arquivo de eleitores '{voters_file}' não encontrado. Iniciando com eleitores vazios.")
        return {} # username: password

    def _save_votes(self):
        """Salva os votos em um arquivo JSON. Simples persistência."""
        votes_file = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'data', f'votes_{self.server_id}.json')
        with open(votes_file, 'w') as f:
            json.dump(self.votes, f, indent=4)

    def _save_voted_users(self):
        """Salva os usuários que votaram em um arquivo JSON."""
        voted_users_file = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'data', f'voted_users_{self.server_id}.json')
        with open(voted_users_file, 'w') as f:
            json.dump(list(self.voted_users), f, indent=4)

    def _load_state(self):
        """
        Carrega o estado (votos e usuários que votaram) de arquivos locais.
        Esta é a base antes da sincronização com outros nós.
        """
        votes_file = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'data', f'votes_{self.server_id}.json')
        voted_users_file = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'data', f'voted_users_{self.server_id}.json')

        with self.lock: # Proteger o acesso ao estado durante o carregamento/sincronização
            if os.path.exists(votes_file):
                with open(votes_file, 'r') as f:
                    self.votes = json.load(f)
            else:
                self.votes = {}

            if os.path.exists(voted_users_file):
                with open(voted_users_file, 'r') as f:
                    self.voted_users = set(json.load(f))
            else:
                self.voted_users = set()
            print(f"[{self.server_id}] Estado carregado localmente: Votos: {self.votes}, Eleitores que já votaram: {self.voted_users}")

    def _sync_with_other_servers(self):
        """
        Tenta sincronizar o estado com outro servidor ativo ao iniciar.
        Ele vai pedir o estado completo de um servidor e substituir o seu próprio.
        """
        print(f"[{self.server_id}] Tentando sincronizar estado com outros servidores...")
        self.discover_other_servers() # Re-descobre os servidores

        if not self.other_servers_uris:
            print(f"[{self.server_id}] Nenhuma outra réplica ativa para sincronizar. Iniciando com estado local.")
            return

        for other_server_id, uri in self.other_servers_uris.items():
            try:
                other_server_proxy = Pyro4.Proxy(uri)
                print(f"[{self.server_id}] Solicitando estado completo de '{other_server_id}' em {uri}...")
                
                # Chamada remota para obter o estado completo
                full_state = other_server_proxy.get_full_state()
                
                with self.lock:
                    self.votes = full_state['votes']
                    self.voted_users = set(full_state['voted_users'])
                    self._save_votes() # Persiste o estado sincronizado
                    self._save_voted_users()
                print(f"[{self.server_id}] Sincronização completa com '{other_server_id}'. Estado atualizado.")
                print(f"[{self.server_id}] Novo estado: Votos: {self.votes}, Eleitores que já votaram: {self.voted_users}")
                return # Sincronizado com sucesso com um servidor, podemos parar.
            except (Pyro4.errors.CommunicationError, Pyro4.errors.NamingError, Pyro4.errors.TimeoutError) as e:
                print(f"[{self.server_id}] Erro ao sincronizar com '{other_server_id}' ({uri}): {e}. Tentando o próximo...")
            except Exception as e:
                print(f"[{self.server_id}] Erro inesperado ao sincronizar com '{other_server_id}' ({uri}): {e}")

        print(f"[{self.server_id}] Não foi possível sincronizar com nenhum outro servidor ativo. Iniciando com estado local (potencialmente desatualizado).")


    def discover_other_servers(self):
        """Descobre outros servidores de votação e armazena seus URIs."""
        ns = Pyro4.locateNS(host=NAME_SERVER_HOST, port=NAME_SERVER_PORT)
        all_servers = ns.list(prefix=VOTING_SERVER_NAME_PREFIX)
        # print(f"[{self.server_id}] Servidores descobertos no Name Server: {all_servers}")

        new_other_servers_uris = {}
        for name, uri in all_servers.items():
            current_id = name.replace(VOTING_SERVER_NAME_PREFIX, '')
            if current_id != self.server_id:
                new_other_servers_uris[current_id] = uri
        self.other_servers_uris = new_other_servers_uris
        # print(f"[{self.server_id}] Outros servidores para sincronização: {self.other_servers_uris}")


    @Pyro4.expose
    def authenticate_voter(self, username, password):
        """Autentica um eleitor."""
        with self.lock:
            if username in self.voters and self.voters[username] == password:
                print(f"[{self.server_id}] Eleitor '{username}' autenticado com sucesso.")
                return True
            print(f"[{self.server_id}] Falha na autenticação para '{username}'.")
            return False

    @Pyro4.expose
    def has_voted(self, username):
        """Verifica se o eleitor já votou."""
        with self.lock:
            return username in self.voted_users

    @Pyro4.expose
    def cast_vote(self, username, candidate):
        """Recebe um voto de um eleitor."""
        print(f"[{self.server_id}] Tentativa de voto: eleitor='{username}', candidato='{candidate}'")

        with self.lock: # Proteger o estado durante a validação e atualização local
            if username not in self.voters:
                print(f"[{self.server_id}] Erro: Eleitor '{username}' não registrado.")
                return False, "Eleitor não registrado."

            if username in self.voted_users:
                print(f"[{self.server_id}] Erro: Eleitor '{username}' já votou.")
                return False, "Você já votou."

            # Atualiza o estado localmente (antes da replicação para consistência imediata)
            self.votes[candidate] = self.votes.get(candidate, 0) + 1
            self.voted_users.add(username)
            print(f"[{self.server_id}] Voto de '{username}' para '{candidate}' registrado localmente.")

        # Tenta replicar o voto para outros servidores (fora do lock principal se for assíncrono, mas aqui está síncrono)
        success, message = self._replicate_vote(username, candidate)
        
        with self.lock: # Proteger o estado ao persistir ou reverter
            if success:
                self._save_votes() # Persiste o estado local
                self._save_voted_users()
                print(f"[{self.server_id}] Voto de '{username}' para '{candidate}' processado e replicado com sucesso.")
                return True, "Voto registrado com sucesso!"
            else:
                # Se a replicação falhar, reverte o voto local (depende da estratégia de consistência)
                print(f"[{self.server_id}] Erro: Falha na replicação do voto. Revertendo estado local.")
                self.votes[candidate] -= 1
                if self.votes[candidate] == 0:
                    del self.votes[candidate]
                self.voted_users.remove(username)
                self._save_votes() # Persiste a reversão
                self._save_voted_users()
                return False, message


    def _replicate_vote(self, username, candidate):
        """Tenta replicar o voto para outros servidores.
           Neste modelo, usamos um "quórum" simples: se a maioria dos outros servidores confirmar, consideramos OK.
        """
        print(f"[{self.server_id}] Iniciando replicação para outros servidores...")
        self.discover_other_servers() # Re-descobre os servidores para pegar novos ou remover caídos

        successful_replications = 0
        total_replicas_to_contact = len(self.other_servers_uris)

        if total_replicas_to_contact == 0:
            print(f"[{self.server_id}] Nenhuma outra réplica para sincronizar. Voto localmente consistente.")
            return True, "Voto processado localmente."

        # Para cada outro servidor, tente enviar o voto para que ele atualize seu estado.
        for other_server_id, uri in self.other_servers_uris.items():
            if other_server_id == self.server_id: # Evita tentar replicar para si mesmo
                continue
            try:
                other_server_proxy = Pyro4.Proxy(uri)
                # Chamamos um método interno para que o outro servidor atualize o estado
                print(f"[{self.server_id}] Replicando voto para '{other_server_id}' em {uri}...")
                is_ok, msg = other_server_proxy.internal_update_state(username, candidate)
                if is_ok:
                    successful_replications += 1
                    print(f"[{self.server_id}] Replicação bem-sucedida para '{other_server_id}'.")
                else:
                    print(f"[{self.server_id}] Replicação falhou para '{other_server_id}': {msg}")
            except (Pyro4.errors.CommunicationError, Pyro4.errors.NamingError, Pyro4.errors.TimeoutError) as e:
                print(f"[{self.server_id}] Erro de comunicação com '{other_server_id}' ({uri}): {e}")
            except Exception as e:
                print(f"[{self.server_id}] Erro inesperado ao replicar para '{other_server_id}' ({uri}): {e}")

        # Condição de sucesso para replicação (exemplo: basta que o nó local e pelo menos um outro nó tenha recebido)
        # Ou uma maioria (quórum)
        # Se eu sou o único servidor, total_replicas_to_contact é 0, required_successes será (0+1)//2 = 0
        # Isso significa que 0 sucessos + 1 (local) >= 0, o que é sempre verdadeiro.
        # Se há 1 outro servidor, total_replicas_to_contact é 1, required_successes será (1+1)//2 = 1
        # Isso significa que 1 sucesso + 1 (local) >= 1 é verdadeiro.
        # Ou seja, basta que pelo menos um dos outros servidores responda.
        required_successes = (total_replicas_to_contact + 1) // 2

        if (successful_replications + 1) >= required_successes: # +1 para contar o próprio servidor
            print(f"[{self.server_id}] Replicação concluída. Sucesso na maioria ({successful_replications+1}/{total_replicas_to_contact+1} nós).")
            return True, "Voto replicado e processado com sucesso."
        else:
            print(f"[{self.server_id}] Falha na replicação. Apenas {successful_replications+1} nós atualizaram o estado.")
            return False, "Falha na replicação do voto para a maioria dos nós."


    @Pyro4.expose
    def internal_update_state(self, username, candidate):
        """
        Método interno para ser chamado por outros servidores para sincronização.
        Recebe um voto já validado por outro nó e apenas atualiza o estado local.
        """
        print(f"[{self.server_id}] Recebido pedido de atualização de estado (voto) para '{username}'->'{candidate}'.")
        with self.lock:
            if username in self.voted_users:
                # Conflito de estado, este eleitor já votou nesta réplica.
                # Em um sistema real, isso exigiria lógica de resolução de conflitos mais avançada (ex: timestamps, CRDTs).
                # Para simplificar, vamos retornar uma falha.
                print(f"[{self.server_id}] Erro interno: Eleitor '{username}' já votou nesta réplica. (Conflito de estado!)")
                return False, "Conflito de estado detectado: eleitor já votou."

            self.votes[candidate] = self.votes.get(candidate, 0) + 1
            self.voted_users.add(username)
            self._save_votes()
            self._save_voted_users()
            print(f"[{self.server_id}] Estado local atualizado por replicação: '{username}' para '{candidate}'.")
            return True, "Estado atualizado com sucesso."

    @Pyro4.expose
    def get_results(self):
        """Retorna os resultados atuais da votação."""
        print(f"[{self.server_id}] Requisição de resultados recebida.")
        with self.lock:
            return dict(self.votes) # Retorna uma cópia para evitar modificações externas

    @Pyro4.expose
    def get_full_state(self):
        """
        Retorna o estado completo do servidor (votos e eleitores que já votaram).
        Usado para sincronização de nós que retornam.
        """
        print(f"[{self.server_id}] Requisição de estado completo recebida.")
        with self.lock:
            return {
                'votes': dict(self.votes),
                'voted_users': list(self.voted_users) # Converte set para list para serialização JSON
            }

    def run(self):
        """Inicia o daemon Pyro e registra o servidor."""
        # Carrega o estado inicial ao iniciar
        self._load_state()

        daemon = Pyro4.Daemon(host=NAME_SERVER_HOST)
        ns = Pyro4.locateNS(host=NAME_SERVER_HOST, port=NAME_SERVER_PORT)

        # Registra o servidor com um nome único
        uri = daemon.register(self, VOTING_SERVER_NAME_PREFIX + self.server_id)
        ns.register(VOTING_SERVER_NAME_PREFIX + self.server_id, uri)

        print(f"[{self.server_id}] Servidor de Votação registrado com nome: '{VOTING_SERVER_NAME_PREFIX + self.server_id}'")
        print(f"[{self.server_id}] URI: {uri}")
        print(f"[{self.server_id}] Daemon Pyro pronto para escutar requisições...")

        # *** NOVO: Sincroniza com outros servidores após o registro ***
        self._sync_with_other_servers()

        daemon.requestLoop() # Inicia o loop de eventos do Pyro

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Uso: python voting_server.py <server_id>")
        print("Ex: python voting_server.py server1")
        print("Ex: python voting_server.py server2")
        sys.exit(1)

    server_id = sys.argv[1]

    # Crie um arquivo de eleitores simples se não existir

    server = VotingServer(server_id)
    server.run()