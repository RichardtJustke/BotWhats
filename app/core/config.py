import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

OPENWA_WEBHOOK_SECRET = os.getenv("OPENWA_WEBHOOK_SECRET", "dev-secret")
OPENWA_API_URL = os.getenv("OPENWA_API_URL", "http://localhost:2785/api")
OPENWA_API_KEY = os.getenv("OPENWA_API_KEY")
MONGO_URI = os.getenv("MONGO_URI", "mongodb://localhost:27018")
MONGO_DB_NAME = os.getenv("MONGO_DB_NAME", "chat_service")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")
# Lista separada por vírgula, em ordem de preferência: se um modelo falhar
# (fora do ar, removido, rate limit, resposta vazia), tenta o próximo.
OPENROUTER_MODELS = [
    m.strip()
    for m in os.getenv(
        "OPENROUTER_MODELS",
        "inclusionai/ling-3.0-flash-sante:free,qwen/qwen3.8-27b:free,dots-studio/dots-3-note-preview:free",
    ).split(",")
    if m.strip()
]
MYSQL_HOST = os.getenv("MYSQL_HOST", "localhost")
MYSQL_PORT = int(os.getenv("MYSQL_PORT", "3306"))
MYSQL_USER = os.getenv("MYSQL_USER", "chatservice")
MYSQL_DB = os.getenv("MYSQL_DB", "beneficiarios")
MYSQL_PASSWORD = os.getenv("MYSQL_PASSWORD")
# Máximo de conexões simultâneas com o MySQL; pedidos além disso esperam na fila
MYSQL_POOL_MIN = int(os.getenv("MYSQL_POOL_MIN", "1"))
MYSQL_POOL_MAX = int(os.getenv("MYSQL_POOL_MAX", "10"))
# Minutos sem mensagem até o bot perguntar "posso ajudar em algo mais?"
SESSION_TIMEOUT_MINUTES = int(os.getenv("SESSION_TIMEOUT_MINUTES", "60"))
# Minutos esperando resposta a essa pergunta até encerrar o atendimento
SESSION_CLOSE_MINUTES = int(os.getenv("SESSION_CLOSE_MINUTES", "10"))
# Conversa encaminhada pra humano: o bot fica em silêncio até passar esse tempo
# sem mensagens; depois a próxima mensagem abre um atendimento novo com o bot
ESCALATION_RELEASE_MINUTES = int(os.getenv("ESCALATION_RELEASE_MINUTES", "120"))
# Horário do atendimento humano, no fuso de Brasília. Dias: 0=segunda ... 6=domingo.
# Fora dele, pedido de cancelamento recebe o passo a passo do app em vez de ir pra fila.
ATENDIMENTO_DIAS = {int(d) for d in os.getenv("ATENDIMENTO_DIAS", "0,1,2,3,4").split(",") if d.strip()}
ATENDIMENTO_INICIO = os.getenv("ATENDIMENTO_INICIO", "08:00")
ATENDIMENTO_FIM = os.getenv("ATENDIMENTO_FIM", "18:00")
# boleto-service (faturas em aberto por CPF); sem URL/chave a consulta fica desligada
BOLETO_SERVICE_URL = os.getenv("BOLETO_SERVICE_URL", "http://boleto-service:3308").rstrip("/")
BOLETO_SERVICE_API_KEY = os.getenv("BOLETO_SERVICE_API_KEY")
# Identidade da empresa e do bot. Também preenchem os {{placeholders}} da
# knowledge_base/ na indexação (ver scripts/ingest.py)
BOT_NAME = os.getenv("BOT_NAME", "Iza")
COMPANY_NAME = os.getenv("COMPANY_NAME", "Acme Benefícios")
COMPANY_APP_NAME = os.getenv("COMPANY_APP_NAME", "Acme Clientes")
SUPPORT_EMAIL = os.getenv("SUPPORT_EMAIL", "atendimento@acme.example")
OMBUDSMAN_EMAIL = os.getenv("OMBUDSMAN_EMAIL", "ouvidoria@acme.example")
SUPPORT_PHONE = os.getenv("SUPPORT_PHONE", "0800 000 0000")
SUPPORT_WHATSAPP = os.getenv("SUPPORT_WHATSAPP", "(11) 90000-0000")
# Links do app nas lojas; vazios = não aparecem nas mensagens
APP_STORE_URL = os.getenv("APP_STORE_URL", "")
PLAY_STORE_URL = os.getenv("PLAY_STORE_URL", "")

KB_PLACEHOLDERS = {
    "bot": BOT_NAME,
    "empresa": COMPANY_NAME,
    "app": COMPANY_APP_NAME,
    "email_atendimento": SUPPORT_EMAIL,
    "email_ouvidoria": OMBUDSMAN_EMAIL,
    "telefone": SUPPORT_PHONE,
    "whatsapp": SUPPORT_WHATSAPP,
}


def fill_placeholders(text: str) -> str:
    """Troca {{empresa}}, {{app}} etc. pelos valores configurados."""
    for key, value in KB_PLACEHOLDERS.items():
        text = text.replace("{{" + key + "}}", value)
    return text


PROMPTS_DIR = Path(__file__).resolve().parent.parent.parent / "prompts"


def load_prompt(name: str) -> str:
    """Lê prompts/<name>.md já com os dados da empresa preenchidos."""
    return fill_placeholders((PROMPTS_DIR / f"{name}.md").read_text(encoding="utf-8")).strip()

_REQUIRED_VARS = {
    "OPENWA_API_KEY": OPENWA_API_KEY,
    "OPENROUTER_API_KEY": OPENROUTER_API_KEY,
    "MYSQL_PASSWORD": MYSQL_PASSWORD,
}


def validate() -> None:
    missing = [name for name, value in _REQUIRED_VARS.items() if not value]
    if missing:
        raise RuntimeError(
            "Variáveis de ambiente obrigatórias não configuradas: "
            f"{', '.join(missing)}. Copie .env.example para .env e preencha os valores."
        )


validate()