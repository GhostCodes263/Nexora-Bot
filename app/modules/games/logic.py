"""Pure game rules (no Telegram, no database) so they are easy to test."""
from __future__ import annotations

import random
import secrets

_RNG = random.SystemRandom()

SLOT_SYMBOLS = ["🍒", "🍋", "🍇", "🔔", "💎"]
RED = {1, 3, 5, 7, 9, 12, 14, 16, 18, 19, 21, 23, 25, 27, 30, 32, 34, 36}


def spin_slots() -> list[str]:
    return [secrets.choice(SLOT_SYMBOLS) for _ in range(3)]


def slots_multiplier_x10(reels: list[str]) -> int:
    """Return the total payout multiplier times 10 (so 15 means 1.5x). 0 = loss."""
    if len(set(reels)) == 1:
        return 100 if reels[0] == "💎" else 40
    if len(set(reels)) == 2:
        return 15
    return 0


def roulette_color(n: int) -> str:
    return "green" if n == 0 else ("red" if n in RED else "black")


def roulette_multiplier(choice: str, number: int) -> int:
    """Total payout multiplier (0 = loss) for an outside bet or a straight-up number."""
    choice = choice.lower()
    if choice.isdigit():
        return 36 if 0 <= int(choice) <= 36 and int(choice) == number else 0
    if choice in ("red", "black"):
        return 2 if roulette_color(number) == choice else 0
    if choice == "green":
        return 14 if number == 0 else 0
    if choice == "even":
        return 2 if number != 0 and number % 2 == 0 else 0
    if choice == "odd":
        return 2 if number % 2 == 1 else 0
    return -1  # invalid bet


def rps_result(player: str, bot: str) -> str:
    if player == bot:
        return "draw"
    return "win" if (player, bot) in {("rock", "scissors"), ("paper", "rock"), ("scissors", "paper")} else "loss"


def new_deck() -> list[int]:
    deck = [rank for rank in range(1, 14) for _ in range(4)]
    _RNG.shuffle(deck)
    return deck


def hand_value(cards: list[int]) -> int:
    total = sum(11 if c == 1 else min(c, 10) for c in cards)
    aces = cards.count(1)
    while total > 21 and aces:
        total -= 10
        aces -= 1
    return total


def card_name(rank: int) -> str:
    return {1: "A", 11: "J", 12: "Q", 13: "K"}.get(rank, str(rank))


def show_hand(cards: list[int]) -> str:
    return " ".join(card_name(c) for c in cards)


def ttt_winner(board: list[str]) -> str | None:
    lines = [(0, 1, 2), (3, 4, 5), (6, 7, 8), (0, 3, 6), (1, 4, 7), (2, 5, 8), (0, 4, 8), (2, 4, 6)]
    for a, b, c in lines:
        if board[a] and board[a] == board[b] == board[c]:
            return board[a]
    return "draw" if all(board) else None


def hangman_mask(word: str, guessed: set[str]) -> str:
    return " ".join(ch if ch in guessed else "_" for ch in word)


def scramble(word: str) -> str:
    letters = list(word)
    for _ in range(10):
        _RNG.shuffle(letters)
        if "".join(letters) != word:
            break
    return "".join(letters)


def math_problem() -> tuple[str, int]:
    a, b = secrets.randbelow(40) + 10, secrets.randbelow(20) + 2
    op = secrets.choice("+-*")
    answer = a + b if op == "+" else a - b if op == "-" else a * b
    return f"{a} {op} {b}", answer


WORDS = ["python", "planet", "garden", "rocket", "guitar", "pirate", "castle", "dragon", "forest", "island",
         "jungle", "market", "puzzle", "silver", "winter", "yellow", "bridge", "candle", "dinner", "engine",
         "flower", "global", "harbor", "legend", "mirror", "orange", "pencil", "rabbit", "season", "ticket",
         "umbrella", "volcano", "whistle", "balloon", "compass", "diamond", "elephant", "festival", "lantern", "mountain"]

# (category, question, correct answer, three wrong answers)
TRIVIA: list[tuple[str, str, str, list[str]]] = [
    ("science", "What is the chemical symbol for gold?", "Au", ["Ag", "Gd", "Go"]),
    ("science", "Which planet is known as the Red Planet?", "Mars", ["Venus", "Jupiter", "Mercury"]),
    ("science", "How many bones does an adult human body have?", "206", ["186", "212", "300"]),
    ("science", "Which gas do plants absorb from the air for photosynthesis?", "Carbon dioxide", ["Oxygen", "Nitrogen", "Helium"]),
    ("science", "What is the hardest natural substance?", "Diamond", ["Gold", "Quartz", "Steel"]),
    ("science", "Which is the largest planet in our solar system?", "Jupiter", ["Saturn", "Neptune", "Earth"]),
    ("science", "What is H2O commonly known as?", "Water", ["Salt", "Hydrogen peroxide", "Ammonia"]),
    ("geography", "What is the capital of Australia?", "Canberra", ["Sydney", "Melbourne", "Perth"]),
    ("geography", "Which is the largest ocean on Earth?", "Pacific", ["Atlantic", "Indian", "Arctic"]),
    ("geography", "What is the capital of Canada?", "Ottawa", ["Toronto", "Vancouver", "Montreal"]),
    ("geography", "In which mountain range is Mount Everest?", "Himalayas", ["Andes", "Alps", "Rockies"]),
    ("geography", "What is the smallest country in the world by area?", "Vatican City", ["Monaco", "Malta", "San Marino"]),
    ("geography", "Which is the largest hot desert in the world?", "Sahara", ["Gobi", "Kalahari", "Arabian"]),
    ("geography", "What is the capital of Japan?", "Tokyo", ["Kyoto", "Osaka", "Nagoya"]),
    ("history", "Who was the first President of the United States?", "George Washington", ["Abraham Lincoln", "Thomas Jefferson", "John Adams"]),
    ("history", "In which year did World War II end?", "1945", ["1939", "1944", "1950"]),
    ("history", "Who painted the Mona Lisa?", "Leonardo da Vinci", ["Michelangelo", "Raphael", "Van Gogh"]),
    ("history", "Which empire built the Colosseum in Rome?", "Roman", ["Greek", "Ottoman", "Persian"]),
    ("history", "In which year did the Berlin Wall fall?", "1989", ["1979", "1991", "1985"]),
    ("history", "Which ancient wonder near Cairo still stands today?", "Great Pyramid of Giza", ["Colossus of Rhodes", "Hanging Gardens", "Lighthouse of Alexandria"]),
    ("tech", "Which company did Bill Gates co-found with Paul Allen?", "Microsoft", ["Apple", "IBM", "Intel"]),
    ("tech", "What does HTML stand for?", "HyperText Markup Language", ["High Tech Machine Language", "Hyperlink Text Mode Language", "Home Tool Markup Language"]),
    ("tech", "What does CPU stand for?", "Central Processing Unit", ["Computer Personal Unit", "Central Program Utility", "Core Processing Update"]),
    ("tech", "The Python language is named after which comedy group?", "Monty Python", ["The Goodies", "Blackadder", "The Two Ronnies"]),
    ("tech", "What is the base of the binary number system?", "2", ["8", "10", "16"]),
    ("tech", "In which year was the first iPhone released?", "2007", ["2005", "2009", "2010"]),
    ("general", "How many days are there in a leap year?", "366", ["365", "364", "367"]),
    ("general", "How many colors are in a rainbow?", "7", ["5", "6", "8"]),
    ("general", "What is the largest mammal in the world?", "Blue whale", ["African elephant", "Giraffe", "Orca"]),
    ("general", "Which is the fastest land animal?", "Cheetah", ["Lion", "Greyhound", "Horse"]),
    ("general", "How many continents are there?", "7", ["5", "6", "8"]),
    ("general", "How many sides does a hexagon have?", "6", ["5", "7", "8"]),
    ("general", "How many legs does a spider have?", "8", ["6", "10", "12"]),
]
TRIVIA_CATEGORIES = sorted({t[0] for t in TRIVIA})
