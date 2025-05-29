import Pyro4
import Pyro4.errors
import sys
import os
import time

# Adiciona o diretório 'common' ao sys.path para importação
current_dir = os.path.dirname(os.path.abspath(__file__))
project_root = os.path.join(current_dir, '..')
sys.path.append(project_root)

from common.constants import VOTING_SERVER_NAME_PREFIX, NAME_SERVER_HOST, NAME_SERVER_PORT

# Define um timeout para as operações de rede Pyro.
# Isso ajuda a detectar servidores lentos ou caídos mais rapidamente.
Pyro4.config.COMMTIMEOUT = 5.0 # Timeout de 5 segundos

class VoterClient:
    def __init__(self):
        self.voting_server_proxy = None
        self.server_id = None
        self.ns = None
        # Armazena as credenciais e o estado do usuário para retentativas transparentes.
        self.current_username = None
        self.current_password = None
        self.max_retries = 3 # Número máximo de tentativas de reconexão/retry por operação
        self.retry_delay = 2 # Atraso em segundos entre as retentativas

    def _get_server_proxy(self):
        """
        Conecta-se ao Name Server e tenta encontrar um servidor de votação disponível.
        Se já houver um proxy ativo, tenta usá-lo; caso contrário, busca um novo.
        """
        # Se já temos um proxy, tentamos usá-lo primeiro.
        if self.voting_server_proxy:
            try:
                # Uma chamada simples para verificar se o servidor ainda está vivo e responsivo.
                # Não é o ideal em produção, mas serve para este contexto.
                _ = self.voting_server_proxy.get_results()
                return self.voting_server_proxy
            except (Pyro4.errors.CommunicationError, Pyro4.errors.NamingError):
                print("Conexão existente com o servidor falhou. Tentando reconectar...")
                self.voting_server_proxy = None # Limpa o proxy falho para buscar um novo

        # Se não há proxy ativo ou o anterior falhou, tentamos obter um novo.
        if not self.ns:
            try:
                self.ns = Pyro4.locateNS(host=NAME_SERVER_HOST, port=NAME_SERVER_PORT)
            except Pyro4.errors.NamingError as e:
                print(f"Erro ao conectar ao Name Server em {NAME_SERVER_HOST}:{NAME_SERVER_PORT}. "
                      "Certifique-se de que ele está rodando. Erro: {e}")
                return None

        # Lista todos os servidores de votação disponíveis registrados no Name Server.
        available_servers = self.ns.list(prefix=VOTING_SERVER_NAME_PREFIX)
        if not available_servers:
            print("Nenhum servidor de votação disponível no Name Server.")
            return None

        print("Servidores de votação disponíveis:")
        # Tenta conectar-se a cada servidor disponível até encontrar um que responda.
        for name, uri in available_servers.items():
            server_id = name.replace(VOTING_SERVER_NAME_PREFIX, '')
            print(f"  - {server_id}: {uri}")
            try:
                proxy = Pyro4.Proxy(uri)
                # Tenta uma chamada leve para verificar a conectividade e disponibilidade do servidor.
                _ = proxy.get_results()
                self.voting_server_proxy = proxy
                self.server_id = server_id
                print(f"Conectado com sucesso ao servidor de votação '{self.server_id}'.")
                return self.voting_server_proxy
            except (Pyro4.errors.CommunicationError, Pyro4.errors.NamingError, Pyro4.errors.TimeoutError) as e:
                print(f"Não foi possível conectar ao servidor '{server_id}' em {uri}: {e}")
            except Exception as e:
                print(f"Erro inesperado ao conectar ao servidor '{server_id}' em {uri}: {e}")

        print("Não foi possível conectar a nenhum servidor de votação disponível após escanear a lista.")
        return None

    def _execute_remote_call(self, method_name, *args):
        """
        Executa uma chamada de método remoto com lógica de retentativa e failover.
        """
        retries_count = 0
        while retries_count < self.max_retries:
            server = self._get_server_proxy()
            if not server:
                print(f"[{method_name}] Nenhum servidor disponível para executar a operação. Tentativa {retries_count + 1}/{self.max_retries}.")
                retries_count += 1
                time.sleep(self.retry_delay)
                continue

            try:
                # Obtém o método do proxy e o chama com os argumentos.
                method_to_call = getattr(server, method_name)
                result = method_to_call(*args)
                return True, result # Sucesso na execução e o resultado.

            except (Pyro4.errors.CommunicationError, Pyro4.errors.NamingError, Pyro4.errors.TimeoutError) as e:
                print(f"[{method_name}] Erro de comunicação com o servidor atual '{self.server_id}': {e}")
                print(f"[{method_name}] Tentando reconectar a outro servidor... Tentativa {retries_count + 1}/{self.max_retries}.")
                self.voting_server_proxy = None # Força a reconexão na próxima iteração.
                retries_count += 1
                time.sleep(self.retry_delay)
            except Exception as e:
                print(f"[{method_name}] Ocorreu um erro inesperado durante a chamada remota: {e}")
                # Erros inesperados podem ou não justificar uma retentativa, dependendo da natureza do erro.
                # Para este caso, vamos tentar novamente.
                retries_count += 1
                time.sleep(self.retry_delay)

        return False, f"Não foi possível completar a operação '{method_name}' após {self.max_retries} tentativas."

    def run(self):
        """Loop principal do cliente."""
        print("Bem-vindo ao Sistema de Votação Distribuído!")
        self.current_username = input("Digite seu nome de usuário: ").strip()
        self.current_password = input("Digite sua senha: ").strip()

        # --- Autenticação ---
        success, auth_result = self._execute_remote_call("authenticate_voter", self.current_username, self.current_password)
        if not success or not auth_result:
            print(f"Autenticação falhou: {auth_result if not success else 'Credenciais inválidas.'}")
            return

        print(f"Eleitor '{self.current_username}' autenticado com sucesso.")

        # --- Verificação de Voto ---
        success, has_voted_result = self._execute_remote_call("has_voted", self.current_username)
        if not success:
            print(f"Erro ao verificar se já votou: {has_voted_result}")
            return
        
        if has_voted_result: # Se o resultado foi True
            print("Você já votou anteriormente.")
            # Se já votou, podemos mostrar os resultados e sair.
            success, results = self._execute_remote_call("get_results")
            if success:
                print("\nResultados Atuais:")
                for candidate, votes in results.items():
                    print(f"- {candidate}: {votes} votos")
            else:
                print(f"Não foi possível obter os resultados: {results}")
            return

        # --- Processo de Voto ---
        print("\nCandidatos disponíveis (digite o nome para votar):")
        print("- Candidato A")
        print("- Candidato B")
        print("- Candidato C")
        candidate = input("Para qual candidato você deseja votar? ").strip()

        success, message = self._execute_remote_call("cast_vote", self.current_username, candidate)

        if success:
            print(f"SUCESSO: {message}")
        else:
            print(f"FALHA: {message}")
            # Em caso de falha no cast_vote, o cliente não tenta votar novamente
            # para evitar votos duplicados, pois o servidor já tentou replicar.
            # O usuário pode ser instruído a tentar novamente mais tarde.

        # --- Obter Resultados Finais ---
        success, results = self._execute_remote_call("get_results")
        if success:
            print("\nResultados Atuais:")
            for candidate, votes in results.items():
                print(f"- {candidate}: {votes} votos")
        else:
            print(f"Não foi possível obter os resultados finais: {results}")


if __name__ == "__main__":
    client = VoterClient()
    client.run()