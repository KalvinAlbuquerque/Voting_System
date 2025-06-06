import logging
# common/constants.py
# Nomes de serviço para registro no Pyro Name Server
VOTING_SERVER_NAME_PREFIX = "voting.server."
VOTER_REGISTRATION_SERVER_NAME = "voter.registration.server"

#Configurações do Name Server
NAME_SERVER_HOST = "localhost"
NAME_SERVER_PORT = 9090 # Porta padrão do Pyro Name Server

# Configuração de logging
LOG_FORMAT = '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
LOG_LEVEL = logging.INFO 