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
from rich.box import MINIMAL # Para um estilo de tabela mais limpo

# Adiciona o diretório 'common' ao sys.path para importação
current_dir = os.path.dirname(os.path.abspath(__file__))
project_root = os.path.join(current_dir, '..')
sys.path.append(project_root)

from common.constants import VOTING_SERVER_NAME_PREFIX, NAME_SERVER_HOST, NAME_SERVER_PORT, LOG_FORMAT, LOG_LEVEL

# Configura o logging para o cliente
logging.basicConfig(level=LOG_LEVEL, format=LOG_FORMAT)
logger = logging.getLogger(__name__)

# Configura o console Rich para saída
console = Console()

# Define um timeout para as operações de rede Pyro.
Pyro4.config.COMMTIMEOUT = 5.0 # Timeout de 5 segundos

class VoterClient:
    """
    Classe Cliente para interagir com o Sistema de Votação Distribuído.
    Responsável pela autenticação, votação e consulta de resultados,
    com lógica de retentativa e failover entre servidores.
    Utiliza a biblioteca Rich para uma interface textual mais apresentável no terminal.
    """
    def __init__(self):
        self.voting_server_proxy = None
        self.server_id = None
        self.ns = None
        self.current_username = None
        self.current_password = None
        self.max_retries = 3 # Número máximo de tentativas de reconexão/retry por operação
        self.retry_delay = 2 # Atraso em segundos entre as retentativas

    def _get_server_proxy(self):
        """
        Conecta-se ao Name Server e tenta encontrar um servidor de votação disponível.
        Se já houver um proxy ativo, tenta usá-lo; caso contrário, busca um novo.
        Prioriza um proxy existente se ainda estiver funcional.
        """
        if self.voting_server_proxy:
            try:
                _ = self.voting_server_proxy.get_results()
                return self.voting_server_proxy
            except (Pyro4.errors.CommunicationError, Pyro4.errors.NamingError, Pyro4.errors.TimeoutError):
                logger.warning(f"Conexão existente com o servidor '{self.server_id}' falhou. Tentando reconectar...")
                console.print(f"[yellow]Conexão existente com o servidor '[b]{self.server_id}[/b]' falhou. Tentando reconectar...[/yellow]")
                self.voting_server_proxy = None

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

        table_servers = Table(title="[bold blue]Servidores de Votação Disponíveis[/bold blue]", box=MINIMAL)
        table_servers.add_column("ID do Servidor", style="cyan", no_wrap=True)
        table_servers.add_column("URI", style="green")

        for name, uri in available_servers.items():
            server_id = name.replace(VOTING_SERVER_NAME_PREFIX, '')
            table_servers.add_row(server_id, uri)
        console.print(table_servers)


        for name, uri in available_servers.items():
            server_id = name.replace(VOTING_SERVER_NAME_PREFIX, '')
            try:
                with Pyro4.Proxy(uri) as proxy:
                    _ = proxy.get_results()
                    self.voting_server_proxy = proxy
                    self.server_id = server_id
                    logger.info(f"Conectado com sucesso ao servidor de votação '{self.server_id}'.")
                    console.print(f"[green]Conectado com sucesso ao servidor de votação '[b]{self.server_id}[/b]'[/green]")
                    return self.voting_server_proxy
            except (Pyro4.errors.CommunicationError, Pyro4.errors.NamingError, Pyro4.errors.TimeoutError) as e:
                logger.warning(f"Não foi possível conectar ao servidor '{server_id}' em {uri}: {e}")
                console.print(f"[yellow]Não foi possível conectar ao servidor '[b]{server_id}[/b]' (URI: {uri}).[/yellow] [dim]Detalhes: {e}[/dim]")
            except Exception as e:
                logger.error(f"Erro inesperado ao conectar ao servidor '{server_id}' em {uri}: {e}")
                console.print(f"[red]Erro inesperado ao conectar ao servidor '[b]{server_id}[/b]' (URI: {uri}).[/red] [dim]Detalhes: {e}[/dim]")

        logger.warning("Não foi possível conectar a nenhum servidor de votação disponível após escanear a lista.")
        console.print(Panel("[bold red]Não foi possível conectar a nenhum servidor de votação disponível após escanear a lista. Tente novamente mais tarde.[/bold red]", title="[bold red]Falha na Conexão[/bold red]", border_style="red"))
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
                console.print(f"[yellow][{method_name}] Nenhum servidor disponível para executar a operação. Tentativa {retries_count + 1}/{self.max_retries}.[/yellow]")
                retries_count += 1
                time.sleep(self.retry_delay)
                continue

            try:
                method_to_call = getattr(server, method_name)
                result = method_to_call(*args)
                return True, result

            except (Pyro4.errors.CommunicationError, Pyro4.errors.NamingError, Pyro4.errors.TimeoutError) as e:
                logger.warning(f"[{method_name}] Erro de comunicação com o servidor atual '{self.server_id}': {e}")
                console.print(f"[yellow][{method_name}] Erro de comunicação com o servidor atual '[b]{self.server_id}[/b]': {e}.[/yellow]")
                console.print(f"[yellow][{method_name}] Tentando reconectar a outro servidor... Tentativa {retries_count + 1}/{self.max_retries}.[/yellow]")
                self.voting_server_proxy = None
                retries_count += 1
                time.sleep(self.retry_delay)
            except Exception as e:
                logger.error(f"[{method_name}] Ocorreu um erro inesperado durante a chamada remota: {e}")
                console.print(f"[red][{method_name}] Ocorreu um erro inesperado durante a chamada remota: {e}.[/red]")
                retries_count += 1
                time.sleep(self.retry_delay)

        logger.error(f"Não foi possível completar a operação '{method_name}' após {self.max_retries} tentativas.")
        console.print(Panel(f"[bold red]Não foi possível completar a operação '[b]{method_name}[/b]' após {self.max_retries} tentativas.[/bold red]", title="[bold red]Erro Crítico[/bold red]", border_style="red"))
        return False, f"Não foi possível completar a operação '{method_name}' após {self.max_retries} tentativas."

    def run(self):
        """Loop principal do cliente, guiando o eleitor através do processo de votação."""
        console.print(Panel("[bold green]Bem-vindo ao Sistema de Votação Distribuído![/bold green]", expand=False))

        self.current_username = console.input("[bold blue]Digite seu nome de usuário: [/bold blue]").strip()
        self.current_password = console.input("[bold blue]Digite sua senha: [/bold blue]").strip()

        # --- Autenticação ---
        console.print("[dim]Autenticando...[/dim]")
        success, auth_result = self._execute_remote_call("authenticate_voter", self.current_username, self.current_password)
        if not success or not auth_result:
            logger.error(f"Autenticação falhou: {auth_result if not success else 'Credenciais inválidas.'}")
            console.print(Panel(f"[bold red]Autenticação falhou![/bold red]\n[dim]{auth_result if not success else 'Credenciais inválidas.'}[/dim]", title="[bold red]Erro de Autenticação[/bold red]", border_style="red"))
            return

        logger.info(f"Eleitor '{self.current_username}' autenticado com sucesso.")
        console.print(f"[green]Eleitor '[b]{self.current_username}[/b]' autenticado com sucesso.[/green]")

        # --- Verificação de Voto ---
        console.print("[dim]Verificando se você já votou...[/dim]")
        success, has_voted_result = self._execute_remote_call("has_voted", self.current_username)
        if not success:
            logger.error(f"Erro ao verificar se já votou: {has_voted_result}")
            console.print(Panel(f"[bold red]Erro ao verificar se já votou:[/bold red]\n[dim]{has_voted_result}[/dim]", title="[bold red]Erro[/bold red]", border_style="red"))
            return

        if has_voted_result:
            logger.info("Você já votou anteriormente.")
            console.print(Panel("[bold yellow]Você já votou anteriormente.[/bold yellow]", title="[bold yellow]Informação[/bold yellow]", border_style="yellow"))
            success, results = self._execute_remote_call("get_results")
            if success:
                self._display_results(results)
                logger.info(f"Resultados exibidos: {results}")
            else:
                logger.error(f"Não foi possível obter os resultados: {results}")
                console.print(Panel(f"[bold red]Não foi possível obter os resultados:[/bold red]\n[dim]{results}[/dim]", title="[bold red]Erro[/bold red]", border_style="red"))
            return

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
        success, message = self._execute_remote_call("cast_vote", self.current_username, chosen_candidate)

        if success:
            logger.info(f"Voto SUCCESSO: {message}")
            console.print(Panel(f"[bold green]SUCESSO![/bold green]\n{message}", title="[bold green]Voto Registrado[/bold green]", border_style="green"))
        else:
            logger.error(f"Voto FALHA: {message}")
            console.print(Panel(f"[bold red]FALHA![/bold red]\n{message}", title="[bold red]Erro no Voto[/bold red]", border_style="red"))

        # --- Obter Resultados Finais ---
        console.print("[dim]Obtendo resultados finais...[/dim]")
        success, results = self._execute_remote_call("get_results")
        if success:
            self._display_results(results, final=True)
            logger.info(f"Resultados finais exibidos: {results}")
        else:
            logger.error(f"Não foi possível obter os resultados finais: {results}")
            console.print(Panel(f"[bold red]Não foi possível obter os resultados finais:[/bold red]\n[dim]{results}[/dim]", title="[bold red]Erro[/bold red]", border_style="red"))

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