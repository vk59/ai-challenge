"""Подсчёт токенов: сколько запрос весит и сколько он стоит.

Токены считаются в двух принципиально разных местах, и путать их нельзя:

    ПОСЛЕ запроса   API возвращает usage — это точные числа, гадать не нужно
    ДО запроса      точных чисел взять неоткуда, остаётся оценка

Оценка нужна ровно затем, чтобы узнать цену до того, как заплатил: влезет ли
диалог в окно модели, во что обойдётся следующая реплика, пора ли подрезать
историю. После отправки оценка уже не нужна — есть usage.

Настоящий токенизатор DeepSeek живёт в пакете transformers, а в этом
репозитории нет ни одной внешней зависимости и заводить её ради оценки
не хочется. Поэтому здесь честная посимвольная модель с коэффициентами,
измеренными по живому API (см. demo.py calibrate в дне 8).

Главное, что выяснилось при замере: наивное «4 символа на токен» неверно,
и промахивается оно не на проценты, а в разы.

    русский      3.21 символа на токен
    английский   6.03
    код          3.59
    цифры        1.31

Английский текст почти вдвое дешевле русского при той же длине. А на коротких
строках всё решает не текст вовсе: у запроса есть постоянная обвязка примерно
в 30 токенов, и на слове «привет» она составляет 84% счёта.
"""

import math

# Измерено: пустой запрос («а», один символ) стоит 31 prompt_token.
# Это разметка ролей и служебные токены чата — платится в каждом запросе.
REQUEST_OVERHEAD = 30

# Каждое сообщение в истории несёт служебную разметку сверх своего текста.
#
# Число измерено, а не прикинуто: один и тот же текст отправлен сначала одним
# сообщением, потом разрезанным на два. Всё остальное в запросе неизменно,
# значит разница — это чистая цена разметки.
#
#     две фразы одним сообщением      49 токенов
#     те же две фразы двумя           53 токенов
#     разница                         +4 на два сообщения
#
# Первая версия этого файла ставила здесь 4 наугад, и оценка диалога
# промахивалась на +21.6%: надбавка начислялась вдвое, на роль и на каждое
# сообщение истории.
MESSAGE_OVERHEAD = 2

# Символов на один токен, по типам. Числа не выдуманы — см. calibrate.
RATIOS = {
    "cyrillic": 3.21,
    "latin": 6.03,
    "digit": 1.31,
    "other": 3.00,      # пробелы, пунктуация, всё остальное
}

# $ за миллион токенов, из каталога OpenRouter. Вход и выход стоят по-разному,
# и выход обычно дороже — это важно при оценке диалога.
PRICES = {
    "deepseek-v4-flash": (0.089, 0.177),
    "deepseek-v4-pro": (1.600, 3.200),
    "google/gemma-2-27b-it": (0.650, 0.650),
    "meta-llama/llama-3.2-1b-instruct": (0.027, 0.201),
    "anthropic/claude-opus-4": (15.000, 75.000),
}

# Размер окна модели — сколько токенов помещается в один запрос целиком,
# вместе с ответом. Тоже из каталога.
CONTEXT_LIMITS = {
    "deepseek-v4-flash": 1_048_576,
    "deepseek-v4-pro": 1_048_576,
    "google/gemma-2-27b-it": 8_192,
    "meta-llama/llama-3.2-1b-instruct": 60_000,
    "anthropic/claude-opus-4": 200_000,
}


def classify(text: str) -> dict[str, int]:
    """Разбирает строку по типам символов — от них зависит цена в токенах."""
    counts = {"cyrillic": 0, "latin": 0, "digit": 0, "other": 0}
    for char in text:
        lower = char.lower()
        if "а" <= lower <= "я" or lower == "ё":
            counts["cyrillic"] += 1
        elif "a" <= lower <= "z":
            counts["latin"] += 1
        elif char.isdigit():
            counts["digit"] += 1
        else:
            counts["other"] += 1
    return counts


def estimate_text(text: str) -> int:
    """Во сколько токенов обойдётся сам текст, без обвязки запроса."""
    if not text:
        return 0
    counts = classify(text)
    total = sum(count / RATIOS[kind] for kind, count in counts.items())
    return math.ceil(total)


def estimate_request(
    prompt: str,
    *,
    system: str | None = None,
    history: list[dict] | None = None,
) -> int:
    """Сколько токенов уедет в модель, если отправить это прямо сейчас.

    Считается всё, за что придётся заплатить: обвязка, роль, вся история
    и сам вопрос. Роль и история — это та часть счёта, которую обычно
    забывают, а платится она каждый раз заново.
    """
    total = REQUEST_OVERHEAD + estimate_text(prompt)
    if system:
        total += estimate_text(system) + MESSAGE_OVERHEAD
    for message in history or []:
        total += estimate_text(message.get("content", "")) + MESSAGE_OVERHEAD
    return total


def cost(model: str, prompt_tokens: int, completion_tokens: int) -> float:
    """Стоимость одного обращения в долларах."""
    price_in, price_out = PRICES.get(model, (0.0, 0.0))
    return (prompt_tokens * price_in + completion_tokens * price_out) / 1_000_000


def limit_of(model: str) -> int:
    """Размер окна модели; 0 — если про эту модель ничего не известно."""
    return CONTEXT_LIMITS.get(model, 0)


def money(amount: float) -> str:
    """Доллары в читаемом виде: дроби тут микроскопические."""
    if amount >= 0.01:
        return f"${amount:.3f}"
    if amount >= 0.00001:
        return f"${amount:.5f}"
    return "<$0.00001"


__all__ = ["estimate_text", "estimate_request", "classify", "cost", "limit_of",
           "money", "RATIOS", "PRICES", "CONTEXT_LIMITS", "REQUEST_OVERHEAD",
           "MESSAGE_OVERHEAD"]
