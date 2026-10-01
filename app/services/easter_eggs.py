"""Easter eggs: palavra exata → imagem aleatória, sem passar pelo LLM."""

import random
import time
from dataclasses import dataclass

# Fotos livres do Wikimedia Commons (categoria Nymphicus hollandicus)
_CALOPSITAS = [
    "https://thumb.wikimedia.org/wikipedia/commons/thumb/a/ac/Cockateil.jpg/960px-Cockateil.jpg",
    "https://thumb.wikimedia.org/wikipedia/commons/thumb/0/00/Cockatiel_-_White_Cliffs.jpg/960px-Cockatiel_-_White_Cliffs.jpg",
    "https://thumb.wikimedia.org/wikipedia/commons/thumb/6/6c/Cockatiel_2.jpg/960px-Cockatiel_2.jpg",
    "https://thumb.wikimedia.org/wikipedia/commons/thumb/8/8a/Cockatiel_3.jpg/960px-Cockatiel_3.jpg",
    "https://thumb.wikimedia.org/wikipedia/commons/thumb/8/8a/Cockatiel_6781.jpg/960px-Cockatiel_6781.jpg",
    "https://thumb.wikimedia.org/wikipedia/commons/thumb/c/c5/Nymphicus_hollandicus_Karratha_10.jpg/960px-Nymphicus_hollandicus_Karratha_10.jpg",
    "https://thumb.wikimedia.org/wikipedia/commons/thumb/2/2d/Nymphicus_hollandicus_-perching_on_wires_-Australia-6a.jpg/960px-Nymphicus_hollandicus_-perching_on_wires_-Australia-6a.jpg",
    "https://upload.wikimedia.org/wikipedia/commons/0/0d/Nymphensittichpaar.jpg",
]

# Arte oficial via PokeAPI (#1 = Bulbasaur)
_BULBASAUR = "https://raw.githubusercontent.com/PokeAPI/sprites/master/sprites/pokemon/other/official-artwork/1.png"


@dataclass(frozen=True)
class EasterEgg:
    image_url: str
    caption: str
    history_text: str  # o que fica salvo no histórico da conversa


def _gato() -> EasterEgg:
    # ?t= evita que OpenWA/WhatsApp reaproveitem a mesma imagem em cache
    return EasterEgg(f"https://cataas.com/cat?t={time.time_ns()}", "Miau! 🐱", "[foto de gato] Miau! 🐱")


def _passarinho() -> EasterEgg:
    return EasterEgg(random.choice(_CALOPSITAS), "Fiu fiu! 🐦", "[foto de calopsita] Fiu fiu! 🐦")


def _pokemon() -> EasterEgg:
    return EasterEgg(_BULBASAUR, "Bulbasaur, eu escolho você! 🌱", "[imagem do Bulbasaur] Bulbasaur, eu escolho você! 🌱")


_TRIGGERS = {
    "gato": _gato,
    "passarinho": _passarinho,
    "pokemon": _pokemon,
    "pokémon": _pokemon,
}


def match_easter_egg(message_body: str) -> EasterEgg | None:
    build = _TRIGGERS.get(message_body.strip().lower())
    return build() if build else None
