# Sistema de Votação Distribuído com Pyro e Python

Este projeto demonstra um sistema de votação online com redundância de nós usando Pyro4.

## Estrutura do Projeto

```
distributed_voting_system/
├── common/
│   └── constants.py
├── servers/
│   ├── name_server.py
│   └── voting_server.py
├── clients/
│   └── voter_client.py
├── data/
│   └── voters.json
├── .gitignore
└── README.md
```

## Informações sobre o Pyro
Pyro4 é uma biblioteca de chamada de procedimento remoto (RPC) em Python. Basicamente, ela permite que você execute métodos de objetos Python que estão em outro processo ou até mesmo em outra máquina, como se eles estivessem no seu próprio código local.

### Sintaxes do Pyro

- @Pyro4.Expose
Expõe uma classe ou método para que possam ser utilizadas por clientes que se conectem através do Pyro4.

- @Pyro4.behavior
Define como o Pyro4 criará e gerenciará as instâncias da sua classe quando as requisições remotas chegarem. 
Se setado para "single", apenas uma única instância daquela classe estará rodando no server pyro. Todas as requisições de todos os clientes serão roteadas para essa mesma instância (Parece um Singleton)

Pyro4.behavior(instance_mode="single"): Este é um decorador importante para o gerenciamento de estado compartilhado. Ele instrui o Pyro4 a criar apenas uma única instância de VotingServer para cada servidor físico/processo. Isso significa que todos os clientes que se conectarem a este servidor interagirão com a mesma instância de VotingServer, compartilhando os mesmos atributos (voters, votes, voted_users, etc.). Isso é fundamental para manter a consistência dos dados em uma única réplica e entre elas.

## Comandos para rodar o código
pyro4-ns -p 9090
cliente + server