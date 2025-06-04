# Sistema de Votação Distribuído com Tolerância a Falhas

Este projeto demonstra um sistema de votação online distribuído com foco em **Tolerância a Falhas** e **Concorrência e Coordenação de Processos**, utilizando replicação de servidores e um mecanismo de quorum.

## Características de Sistemas Distribuídos Implementadas

### Tolerância a Falhas
A resiliência a falhas é alcançada através da replicação ativa dos servidores de votação. Se um servidor falhar, o cliente pode automaticamente se reconectar a outro servidor disponível. Novos servidores (ou servidores que retornam após uma falha) sincronizam seu estado com os nós ativos. A operação de votação só é considerada bem-sucedida se for replicada para a maioria dos servidores ativos (quorum).

### Concorrência e Coordenação de Processos
O sistema lida com o acesso concorrente de múltiplos clientes aos dados de votação usando locks de threading para proteger o estado compartilhado. A coordenação entre os servidores é gerenciada através do processo de replicação, onde um servidor atua como coordenador para garantir que todos os votos sejam propagados e confirmados pela maioria das réplicas, mantendo a consistência dos dados em todo o sistema.

## Estrutura do Projeto
distributed_voting_system/
├── common/
│   └── constants.py          # Constantes compartilhadas (nomes de serviço, hosts, portas)
├── servers/
│   ├── voting_server.py      # Lógica do servidor de votação com replicação e quorum
├── clients/
│   └── voter_client.py       # Lógica do cliente eleitor com failover
├── data/
│   ├── votes_serverX.json    # Persistência de votos para cada servidor (ex: votes_server1.json)
├── .gitignore
└── README.md                 # Documentação do projeto e instruções


## Tecnologias Utilizadas

* **Python 3.x**
* **Pyro4**: Biblioteca de Remote Procedure Call (RPC) para comunicação entre processos distribuídos.
* **Rich**: Biblioteca para formatação de saída no terminal (interface do cliente).
* **JSON**: Para persistência de dados de votação em arquivos locais.

## Comandos para Rodar o Código

Para executar o sistema, você precisará de múltiplos terminais.

### 1. Pré-requisitos
Certifique-se de ter Python 3.x instalado.
Instale as bibliotecas necessárias:
```bash
pip install Pyro4 rich 
```

### 2. Iniciar o Name Server (Servidor de Nomes)
O Name Server do Pyro4 é essencial para que os outros componentes se encontrem.
Abra um terminal e execute:
```bash
pyro4-ns -p 9090
```
Este comando inicia o servidor de nomes na porta 9090. Ele deve estar rodando antes de qualquer servidor de votação.

### 3. Iniciar Servidores de Votação (Múltiplas Instâncias)
Abra um novo terminal para cada instância de servidor que você deseja executar. Para demonstrar a tolerância a falhas, é recomendável iniciar pelo menos dois servidores (ou mais).

Exemplo:
Terminal 1:

```bash
python servers/voting_server.py server1
```

Terminal 2:

```bash
python servers/voting_server.py server2
```

Terminal 3 (Opcional, para testes de quorum mais robustos):

```bash
python servers/voting_server.py server3
```

Cada servidor será registrado no Name Server com seu ID (server1, server2, etc.). Ao iniciar, cada servidor tentará sincronizar seu estado com outros servidores já ativos, carregando votos persistidos ou buscando o estado mais recente.

### 4. Iniciar Cliente Eleitor
Abra um novo terminal e execute:

```bash
python clients/voter_client.py
```

O cliente irá se conectar automaticamente a um servidor de votação disponível e apresentará as opções de votação. Ele possui lógica de reconexão e failover, tentando se conectar a outro servidor se o atual falhar

## Simulação e Testes de Tolerância a Falhas

Para demonstrar a tolerância a falhas e a consistência, siga os passos de execução e então:

1.  **Prepare o Ambiente:**
    * Inicie o `pyro4-ns` (Passo 2).
    * Inicie dois ou mais `voting_server.py` (ex: `server1` e `server2`) (Passo 3).
    * Inicie o `voter_client.py` (Passo 4).

2.  **Realize Votos Iniciais:**
    * No cliente, faça alguns votos para diferentes candidatos.
    * Observe nos logs dos servidores que os votos estão sendo replicados entre eles.

3.  **Teste de Falha de Servidor:**
    * No terminal onde um dos servidores (ex: `server1`) está rodando, pressione `Ctrl+C` para encerrá-lo.
    * No cliente, tente fazer mais votos. O cliente detectará a falha de `server1` e tentará se reconectar ao `server2`. A votação deve continuar sem interrupção.
    * Consulte os resultados. Verifique que os votos feitos com `server1` caído são consistentes.

4.  **Teste de Recuperação de Servidor:**
    * Reinicie o `voting_server.py server1` (ou o servidor que você derrubou).
    * Observe os logs do `server1` - ele realizará um processo de sincronização com o `server2` (ou qualquer outro servidor ativo) para obter o estado mais recente dos votos.
    * No cliente, consulte os resultados novamente. Você deve ver que todos os votos (incluindo os feitos enquanto `server1` estava fora do ar) agora estão presentes no `server1` também.

5.  **Teste de Quorum (Requer 3+ Servidores para Melhor Demonstração):**
    * Inicie `pyro4-ns`, `voting_server.py server1`, `voting_server.py server2`, e `voting_server.py server3`.
    * No cliente, inicie um voto para um candidato.
    * Enquanto o cliente aguarda a confirmação do voto (este é o momento em que a replicação está ocorrendo), rapidamente derrube dois dos servidores (ex: `server2` e `server3`) usando `Ctrl+C` em seus terminais.
    * Observe o resultado no cliente: o voto deve falhar (`FALHA! Falha na replicação do voto para a maioria dos nós.`).
    * Verifique os logs do servidor que iniciou o voto (`server1`) - ele deve ter revertido o voto localmente, pois o quorum de replicação não foi atingido.

Este conjunto de testes demonstra a capacidade do sistema de operar sob falhas e de manter a consistência dos dados através da replicação e do mecanismo de quorum.