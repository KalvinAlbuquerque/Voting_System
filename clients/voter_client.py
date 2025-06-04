# clients/voter_client.py
import Pyro4
import Pyro4.errors
import sys
import os
import time
import logging

# Importa as classes necessárias do Rich
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from rich.box import MINIMAL # Para um estilo de tabela mais limpa

# Adiciona o diretório 'common' ao sys.path para importação
current_dir = os.path.dirname(os.path.abspath(__file__))
project_root = os.path.join(current_dir, '..')
sys.path.append(project_root)

from common.constants import VOTING_SERVER_NAME_PREFIX, NAME_SERVER_HOST, NAME_SERVER_PORT, LOG_FORMAT, LOG_LEVEL

# Configura o logging para o cliente - AUMENTAR O NÍVEL PARA EVITAR INFO/DEBUG NO CONSOLE
logging.basicConfig(level=logging.WARNING, format=LOG_FORMAT) # Alterado para WARNING
logger = logging.getLogger(__name__)

# Configura o console Rich para saída
console = Console()

# Define um timeout para as operações de rede Pyro.
Pyro4.config.COMMTIMEOUT = 5.0 # Timeout de 5 segundos

class VoterClient:
    """
    Classe Cliente para interagir com o Sistema de Votação Distribuído.
    Responsável pela votação e consulta de resultados,
    com lógica de retentativa e failover entre servidores.
    Utiliza a biblioteca Rich para uma interface textual mais apresentável no terminal.
    """
    def __init__(self):
        self.voting_server_proxy = None
        self.server_id = None
        self.ns = None
        self.max_retries = 3 # Número máximo de tentativas de reconexão/retry por operação
        self.retry_delay = 2 # Atraso em segundos entre as retentativas
        self._last_connected_server = None # Para evitar mensagens de reconexão redundantes

    def _get_server_proxy(self):
        """
        Conecta-se ao Name Server e tenta encontrar um servidor de votação disponível.
        Se já houver um proxy ativo, tenta usá-lo; caso contrário, busca um novo.
        Prioriza um proxy existente se ainda estiver funcional.
        """
        if self.voting_server_proxy:
            try:
                _ = self.voting_server_proxy.get_results()
                # Se o servidor atual ainda funciona, não precisa logar novamente a conexão
                return self.voting_server_proxy
            except (Pyro4.errors.CommunicationError, Pyro4.errors.NamingError, Pyro4.errors.TimeoutError):
                logger.warning(f"Conexão existente com o servidor '{self.server_id}' falhou. Tentando reconectar...")
                console.print(f"[yellow]Conexão com o servidor '[b]{self.server_id}[/b]' falhou. Tentando reconectar...[/yellow]")
                self.voting_server_proxy = None # Limpar o proxy falho

        if not self.ns:
            try:
                self.ns = Pyro4.locateNS(host=NAME_SERVER_HOST, port=NAME_SERVER_PORT)
            except Pyro4.errors.NamingError as e:
                logger.error(f"Erro ao conectar ao Name Server em {NAME_SERVER_HOST}:{NAME_SERVER_PORT}. "
                             f"Certifique-se de que ele está rodando. Erro: {e}")
                console.print(Panel(f"[bold red]Erro ao conectar ao Name Server![/bold red]\nCertifique-se de que ele está rodando em [cyan]{NAME_SERVER_HOST}:{NAME_SERVER_PORT}[/cyan].\n[dim]Detalhes: {e}[/dim]", title="[bold red]Erro de Conexão[/bold red]", border_style="red"))
                return None

        available_servers = self.ns.list(prefix=VOTING_SERVER_NAME_PREFIX)
        if not available_servers:
            logger.warning("Nenhum servidor de votação disponível no Name Server.")
            console.print(Panel("[bold yellow]Nenhum servidor de votação disponível no Name Server.[/bold yellow]", title="[bold yellow]Aviso[/bold yellow]", border_style="yellow"))
            return None

        # Tentar conectar aos servidores disponíveis
        for name, uri in available_servers.items():
            server_id = name.replace(VOTING_SERVER_NAME_PREFIX, '')
            try:
                with Pyro4.Proxy(uri) as proxy:
                    _ = proxy.get_results() # Testar a conexão
                    self.voting_server_proxy = proxy
                    self.server_id = server_id
                    # Mostrar mensagem de conexão apenas se for uma nova conexão ou reconexão a um servidor diferente
                    if self._last_connected_server != self.server_id:
                        logger.info(f"Conectado com sucesso ao servidor de votação '{self.server_id}'.") # Isso aparece no log
                        console.print(f"[green]Conectado ao servidor '[b]{self.server_id}[/b]'[/green]") # Isso aparece para o usuário
                        self._last_connected_server = self.server_id
                    return self.voting_server_proxy
            except (Pyro4.errors.CommunicationError, Pyro4.errors.NamingError, Pyro4.errors.TimeoutError) as e:
                logger.warning(f"Não foi possível conectar ao servidor '{server_id}' em {uri}: {e}")
                # Não exibir para o usuário final, apenas no log
                # console.print(f"[yellow]Não foi possível conectar ao servidor '[b]{server_id}[/b]' (URI: {uri}).[/yellow] [dim]Detalhes: {e}[/dim]")
            except Exception as e:
                logger.error(f"Erro inesperado ao conectar ao servidor '{server_id}' em {uri}: {e}")
                # Não exibir para o usuário final, apenas no log
                # console.print(f"[red]Erro inesperado ao conectar ao servidor '[b]{server_id}[/b]' (URI: {uri}).[/red] [dim]Detalhes: {e}[/dim]")

        logger.warning("Não foi possível conectar a nenhum servidor de votação disponível após escanear a lista.")
        console.print(Panel("[bold red]Não foi possível conectar a nenhum servidor de votação disponível. Tentando novamente...[/bold red]", title="[bold red]Falha na Conexão[/bold red]", border_style="red"))
        return None

    def _execute_remote_call(self, method_name, *args):
        """
        Executa uma chamada de método remoto com lógica de retentativa e failover.
        Retorna uma tupla (sucesso: bool, resultado/mensagem: any).
        """
        retries_count = 0
        while retries_count < self.max_retries:
            server = self._get_server_proxy()
            if not server:
                logger.warning(f"[{method_name}] Nenhum servidor disponível para executar a operação. Tentativa {retries_count + 1}/{self.max_retries}.")
                # console.print(f"[yellow][{method_name}] Nenhum servidor disponível para executar a operação. Tentativa {retries_count + 1}/{self.max_retries}.[/yellow]") # Remover para reduzir poluição
                retries_count += 1
                time.sleep(self.retry_delay)
                continue

            try:
                method_to_call = getattr(server, method_name)
                # O resultado de cast_vote é uma tupla (bool, str)
                success, message = method_to_call(*args)
                return success, message # Retorna diretamente o sucesso e a mensagem do servidor

            except (Pyro4.errors.CommunicationError, Pyro4.errors.NamingError, Pyro4.errors.TimeoutError) as e:
                logger.warning(f"[{method_name}] Erro de comunicação com o servidor atual '{self.server_id}': {e}")
                # Apenas uma mensagem concisa para o usuário
                console.print(f"[yellow]Servidor '{self.server_id}' falhou. Tentando conectar a outro servidor...[/yellow]")
                self.voting_server_proxy = None
                retries_count += 1
                time.sleep(self.retry_delay)
            except Exception as e:
                logger.error(f"[{method_name}] Ocorreu um erro inesperado durante a chamada remota: {e}")
                console.print(f"[red]Ocorreu um erro inesperado: {e}.[/red]") # Mensagem mais genérica para o usuário
                retries_count += 1
                time.sleep(self.retry_delay)

        logger.error(f"Não foi possível completar a operação '{method_name}' após {self.max_retries} tentativas.")
        # Esta mensagem é o fallback se TODAS as tentativas falharem, sem obter resposta do servidor.
        return False, "Não foi possível completar a operação. O sistema de votação pode estar indisponível."

    def run(self):
        """Loop principal do cliente, guiando o eleitor através do processo de votação."""
        console.print(Panel("[bold green]Bem-vindo ao Sistema de Votação Distribuído![/bold green]", expand=False))

        # Tenta conectar-se ao servidor logo ao iniciar
        initial_connection_successful = False
        console.print("[dim]Conectando ao servidor de votação...[/dim]")
        for _ in range(self.max_retries):
            if self._get_server_proxy():
                initial_connection_successful = True
                break
            console.print(f"[yellow]Tentando conexão inicial em {self.retry_delay} segundos...[/yellow]")
            time.sleep(self.retry_delay)

        if not initial_connection_successful:
            console.print(Panel("[bold red]Não foi possível conectar a nenhum servidor de votação após várias tentativas. Encerrando.[/bold red]", title="[bold red]Erro Fatal[/bold red]", border_style="red"))
            sys.exit(1)


        # --- Processo de Voto ---
        console.print(Panel("[bold blue]Candidatos disponíveis (digite o número correspondente para votar):[/bold blue]", expand=False))
        available_candidates = ["Candidato A", "Candidato B", "Candidato C"]
        candidate_menu = Table(box=MINIMAL)
        candidate_menu.add_column("#", style="dim", no_wrap=True)
        candidate_menu.add_column("Candidato", style="magenta")
        for i, c in enumerate(available_candidates):
            candidate_menu.add_row(str(i+1), c)
        console.print(candidate_menu)

        chosen_candidate = None
        while chosen_candidate is None:
            choice_input = console.input("[bold green]Para qual candidato você deseja votar (número)? [/bold green]").strip()
            try:
                choice_idx = int(choice_input) - 1
                if 0 <= choice_idx < len(available_candidates):
                    chosen_candidate = available_candidates[choice_idx]
                else:
                    console.print("[yellow]Número inválido. Por favor, digite o número correspondente ao candidato.[/yellow]")
            except ValueError:
                console.print("[yellow]Entrada inválida. Por favor, digite um número.[/yellow]")

        console.print(f"[dim]Registrando seu voto para '{chosen_candidate}'...[/dim]")
        success, message = self._execute_remote_call("cast_vote", chosen_candidate) # A mensagem agora virá do servidor

        if success:
            logger.info(f"Voto SUCCESSO: {message}")
            console.print(Panel(f"[bold green]SUCESSO![/bold green]\n{message}", title="[bold green]Voto Registrado[/bold green]", border_style="green"))
        else:
            logger.error(f"Voto FALHA: {message}")
            console.print(Panel(f"[bold red]FALHA![/bold red]\n{message}", title="[bold red]Erro no Voto[/bold red]", border_style="red"))

        console.print("[bold blue]Processo de votação concluído. Encerrando o cliente.[/bold blue]")
        sys.exit(0)


    def _display_results(self, results, final=False):
        """Exibe os resultados da votação usando uma tabela Rich."""
        title = "[bold blue]Resultados Atuais da Votação[/bold blue]"
        if final:
            title = "[bold blue]Resultados Finais da Votação[/bold blue]"

        table = Table(title=title, show_lines=True)
        table.add_column("Candidato", style="cyan", justify="left")
        table.add_column("Votos", style="magenta", justify="right")

        if not results:
            table.add_row("[dim]Nenhum voto registrado ainda.[/dim]", "")
        else:
            sorted_results = sorted(results.items(), key=lambda item: item[1], reverse=True)
            for candidate, votes in sorted_results:
                table.add_row(candidate, str(votes))
        console.print(table)


if __name__ == "__main__":
    client = VoterClient()
    client.run()