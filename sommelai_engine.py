"""TerroirMind AI · SommelAIEngine.

Ideia, curadoria e autoria: Herbert Felipe.

Um só motor para vinhos, destilados, charutos e cafés especiais. Identifica o
rótulo (texto ou foto), harmoniza com o prato do dia, sugere pelo paladar e
monta o Combo Vícios Elegantes (charuto + bebida ou café). O conhecimento
sensorial mora em CATALOGO e PRATOS; a didática completa está em
GUIA_SOMMELIER_DIDATICO.md.

Entrada opcional (--config), chaves planas:

    {
      "tipo_consulta": "Harmonização por Prato",
      "categoria": "Vinho",
      "entrada": "Carne de sol com macaxeira",
      "imagem": ""
    }

O JSON exportado (degustacao_resultado.json) também pode ser recarregado com
--config. Só biblioteca padrão. A leitura de foto usa o Gemini quando a
variável GEMINI_API_KEY está definida; sem ela, o nome do arquivo vira o texto.

Preços de referência vêm de precos_referencia.csv, preenchido à mão (por
exemplo, com o que se vê na DLP Vinhos). O motor nunca raspa sites de lojas.
"""

from __future__ import annotations

import argparse
import base64
import csv
import difflib
import json
import os
import re
import sys
import textwrap
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field, replace
from datetime import date, datetime
from pathlib import Path


ENGINE_VERSION = "1.0.0"
PROJETO = "TerroirMind AI"
AUTOR = "Herbert Felipe"
LEMA = "Do terroir à mente, do gole à memória."
BASE_DIR = Path(__file__).resolve().parent
DEFAULT_OUTPUT = BASE_DIR / "degustacao_resultado.json"
DEFAULT_PRECOS = BASE_DIR / "precos_referencia.csv"
GUIA = "GUIA_SOMMELIER_DIDATICO.md"
HISTORICO_MAX = 50
TOP_PADRAO = 3
TOP_MAX = 10
LARGURA = 76

PRECO_COLUNAS = ("chave", "loja", "produto", "preco", "data")
PRECO_VALIDADE_DIAS = 30
OFERTAS_MAX = 2

GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models"
GEMINI_MODELO_PADRAO = "gemini-flash-latest"
IMAGEM_MAX_BYTES = 15 * 1024 * 1024
MIME_IMAGEM = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
    ".heic": "image/heic",
    ".heif": "image/heif",
}
PROMPT_ROTULO = (
    "A imagem mostra o rótulo de um vinho, de um destilado, o anel de um charuto ou o pacote de um café. "
    "Transcreva numa única linha apenas o que está escrito e identifica o produto: marca ou produtor, "
    "nome do produto, uva ou estilo, idade ou safra e região. Não descreva sabores e não invente nada "
    "que não esteja visível."
)

TIPOS = {
    "rotulo": "Análise de Rótulo",
    "prato": "Harmonização por Prato",
    "paladar": "Sugestão por Paladar",
    "combo": "Combo Vícios Elegantes (Charuto + Bebida/Café)",
}
TIPO_PREFIXOS = (
    ("analise", "rotulo"),
    ("rotulo", "rotulo"),
    ("label", "rotulo"),
    ("harmoniz", "prato"),
    ("prato", "prato"),
    ("sugest", "paladar"),
    ("paladar", "paladar"),
    ("perfil", "paladar"),
    ("combo", "combo"),
    ("vicio", "combo"),
)

CATEGORIAS = {"vinho": "Vinho", "destilado": "Destilado", "charuto": "Charuto", "cafe": "Café"}
CATEGORIA_ALIASES = {
    "vinhos": "vinho",
    "wine": "vinho",
    "destilados": "destilado",
    "spirits": "destilado",
    "charutos": "charuto",
    "cigar": "charuto",
    "cigars": "charuto",
    "charuto cubano": "charuto",
    "cafes": "cafe",
    "coffee": "cafe",
    "cafe especial": "cafe",
    "cafes especiais": "cafe",
}

DEFAULTS: dict[str, object] = {
    "tipo_consulta": "Harmonização por Prato",
    "categoria": "",
    "entrada": "Carne de sol com macaxeira e manteiga de garrafa",
    "imagem": "",
}
ALLOWED_KEYS = set(DEFAULTS)


# ---------------------------------------------------------------------------
# Escalas didáticas (níveis 1 a 5). O texto é o que o iniciante lê.
# ---------------------------------------------------------------------------

CORPO_LIQUIDO = (
    "Como água: desliza leve, quase sem peso na língua.",
    "Como leite desnatado: leve, mas já com alguma presença.",
    "Como leite semidesnatado: médio, preenche a boca sem pesar.",
    "Como leite integral: denso, cobre a língua como veludo.",
    "Como creme de leite: untuoso, quase dá para mastigar.",
)
CORPO_FUMACA = (
    "Fumaça fina como névoa de manhã.",
    "Fumaça leve e sedosa, fácil de conduzir.",
    "Fumaça média, que preenche a boca com calma.",
    "Fumaça densa e cremosa, como espuma de cappuccino.",
    "Fumaça espessa, quase mastigável, como chantilly.",
)
ACIDEZ = (
    "Baixa: macia, quase não faz salivar.",
    "Discreta: frescor sutil, como pera madura.",
    "Média: refresca como morder uma maçã.",
    "Alta: faz a boca salivar, como limonada.",
    "Vibrante: elétrica, como limão-taiti na ponta da língua.",
)
DOCURA = (
    "Seco: nenhum açúcar perceptível.",
    "Quase seco: um fio de doçura de fruta madura.",
    "Meio-doce: doçura que arredonda sem enjoar.",
    "Doce: lembra fruta em calda ou caramelo.",
    "Muito doce: mel e rapadura em estado líquido.",
)
INTENSIDADE = (
    "Discreta: é preciso procurar os aromas.",
    "Suave: aromas presentes, mas tímidos.",
    "Média: aromas claros logo no primeiro contato.",
    "Alta: os aromas saltam do copo.",
    "Exuberante: perfuma o ambiente.",
)
PERSISTENCIA = (
    "Curta: some em menos de 5 segundos.",
    "Curta a média: 5 a 10 segundos.",
    "Média: 10 a 20 segundos.",
    "Longa: 20 a 40 segundos de memória.",
    "Muito longa: mais de 40 segundos; o sabor fica conversando com você.",
)
ESTRUTURA_NOME = {"vinho": "Taninos", "destilado": "Calor alcoólico", "charuto": "Fortaleza", "cafe": "Amargor"}
ESTRUTURA = {
    "vinho": (
        "Quase sem taninos (o normal nos brancos): nada seca a gengiva.",
        "Taninos de seda: um toque fino, mal se nota.",
        "Taninos médios: leve secura na gengiva, como chá preto.",
        "Taninos firmes: secam a boca como casca de uva mastigada.",
        "Taninos poderosos: agarram a gengiva e pedem comida gordurosa.",
    ),
    "destilado": (
        "Calor mínimo: desce macio.",
        "Calor suave: um abraço morno no peito.",
        "Calor médio: aquece a boca; uma gota de água o acalma.",
        "Calor marcante: arde de leve na língua; beba aos golinhos.",
        "Calor intenso: brasa na boca; use água e paciência.",
    ),
    "charuto": (
        "Suave: ideal para o primeiro charuto da vida.",
        "Suave a médio: delicado, com mais sabor que força.",
        "Médio: equilíbrio entre sabor e presença de nicotina.",
        "Médio a forte: pede experiência e estômago forrado.",
        "Forte: potência plena; fume devagar e depois de comer.",
    ),
    "cafe": (
        "Amargor mínimo: o brilho é da fruta, não da torra.",
        "Amargor leve, como cacau ao leite.",
        "Amargor médio, equilibrado com o doce.",
        "Amargor marcante, como chocolate 70%.",
        "Amargor intenso de torra escura, como chocolate 85%.",
    ),
}
EIXOS_VISIVEIS = {
    "vinho": ("corpo", "acidez", "docura", "estrutura", "intensidade"),
    "destilado": ("corpo", "docura", "estrutura", "intensidade"),
    "charuto": ("corpo", "docura", "estrutura", "intensidade"),
    "cafe": ("corpo", "acidez", "docura", "estrutura", "intensidade"),
}
SERVICO_ROTULOS = {
    "vinho": ("Temperatura", "Taça", "Decantação"),
    "destilado": ("Temperatura", "Copo", "Ritual"),
    "charuto": ("Conservação", "Bitola e tempo", "Corte e acendimento"),
    "cafe": ("Água e xícara", "Método", "Receita"),
}


# ---------------------------------------------------------------------------
# Modelos de dados
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Servico:
    temperatura: str
    utensilio: str
    ritual: str


@dataclass(frozen=True)
class Perfil:
    """Ficha sensorial de um estilo (marca vazia) ou de um rótulo específico.

    Níveis de 1 a 5. `estrutura` muda de sentido por categoria: taninos no
    vinho, calor alcoólico no destilado, fortaleza no charuto e amargor no café.
    """

    chave: str
    nome: str
    categoria: str
    estilo: str
    origem: str
    aromas: tuple[str, ...]
    ataque: str
    retrossabor: str
    corpo: int
    acidez: int
    docura: int
    estrutura: int
    intensidade: int
    persistencia: int
    familias: tuple[str, ...]
    servico: Servico
    classicos: tuple[str, ...]
    poesia: str
    sinonimos: tuple[str, ...] = ()
    ficha: tuple[tuple[str, str], ...] = ()
    marca: str = ""
    efervescente: bool = False
    terras: tuple[str, ...] = ()

    @property
    def peso(self) -> int:
        return limitar(arred((2 * self.corpo + self.estrutura + self.intensidade) / 4), 1, 5)

    @property
    def e_rotulo(self) -> bool:
        return bool(self.marca)


@dataclass(frozen=True)
class Prato:
    chave: str
    nome: str
    momento: str  # principal, entrada, petisco, sobremesa ou manha
    peso: int
    gordura: int
    acidez: int
    docura: int
    picancia: int
    sal: int
    familias: tuple[str, ...]
    sinonimos: tuple[str, ...] = ()
    maritimo: bool = False
    terra: str = ""

    def to_dict(self) -> dict[str, object]:
        return {
            "chave": self.chave,
            "nome": self.nome,
            "momento": self.momento,
            "maritimo": self.maritimo,
            "terra": self.terra,
            "peso": self.peso,
            "gordura": self.gordura,
            "acidez": self.acidez,
            "docura": self.docura,
            "picancia": self.picancia,
            "sal": self.sal,
            "familias": list(self.familias),
        }


@dataclass(frozen=True)
class Harmonia:
    chave: str
    nome: str
    categoria: str
    nota: int
    motivos: tuple[str, ...]

    @property
    def veredito(self) -> str:
        return veredito(self.nota)

    def to_dict(self) -> dict[str, object]:
        return {
            "chave": self.chave,
            "nome": self.nome,
            "categoria": self.categoria,
            "nota": self.nota,
            "veredito": self.veredito,
            "motivos": list(self.motivos),
        }


@dataclass(frozen=True)
class Identificacao:
    texto: str
    metodo: str
    confianca: str
    observacao: str = ""

    def to_dict(self) -> dict[str, str]:
        return {
            "texto": self.texto,
            "metodo": self.metodo,
            "confianca": self.confianca,
            "observacao": self.observacao,
        }


@dataclass
class Preferencias:
    alvos: dict[str, int] = field(default_factory=dict)
    gosta: set[str] = field(default_factory=set)
    evita: set[str] = field(default_factory=set)
    termos: list[str] = field(default_factory=list)

    def resumo(self, categoria: str = "") -> str:
        partes = []
        for eixo, nivel in self.alvos.items():
            rotulo = rotulo_eixo(eixo, categoria) if categoria else ROTULO_EIXO[eixo]
            if eixo == "estrutura" and not categoria:
                rotulo = "Força (taninos, álcool, fortaleza ou amargor)"
            partes.append(f"{rotulo} {nivel}/5")
        if self.gosta:
            partes.append("gosta de " + ", ".join(sorted(self.gosta)))
        if self.evita:
            partes.append("evita " + ", ".join(sorted(self.evita)))
        return "; ".join(partes)

    def to_dict(self) -> dict[str, object]:
        return {
            "alvos": dict(self.alvos),
            "gosta": sorted(self.gosta),
            "evita": sorted(self.evita),
            "termos_reconhecidos": list(self.termos),
        }


@dataclass(frozen=True)
class Consulta:
    tipo: str
    categoria: str
    entrada: str
    imagem: Path | None = None
    output: Path = DEFAULT_OUTPUT
    json_only: bool = False
    top: int = TOP_PADRAO
    historico: bool = True


# ---------------------------------------------------------------------------
# Catálogo sensorial. Estilos (sem marca) alimentam as sugestões; rótulos
# (com marca) são alvos de identificação. Rótulo novo só com ficha do produtor.
# ---------------------------------------------------------------------------

UMIDOR = "Umidor a 16–20 °C e 65–70% de umidade relativa; descanse o charuto ali alguns dias antes de fumar."
CORTE_RETO = (
    "Corte reto com guilhotina, 2 mm abaixo da cabeça. Toste o pé com chama de butano ou fósforo de cedro, "
    "girando e sem encostar no fogo. Puxe devagar, uma vez por minuto, e nunca trague."
)
WHISKY_TEMPERATURA = "Temperatura ambiente (18–20 °C). Gelo fecha os aromas."

CATALOGO: tuple[Perfil, ...] = (
    # ----------------------------------------------------------------- Vinhos
    Perfil(
        chave="cabernet-sauvignon", nome="Cabernet Sauvignon", categoria="vinho",
        estilo="Tinto encorpado",
        origem="Bordeaux (França), Vale do Maipo (Chile), Napa Valley (EUA), Serra Gaúcha (Brasil)",
        aromas=("cassis", "amora", "pimentão verde", "cedro", "tabaco"),
        ataque="Seco e firme: fruta negra logo de cara e taninos que agarram a gengiva.",
        retrossabor="Cedro, cassis e um amargor elegante de cacau.",
        corpo=4, acidez=3, docura=1, estrutura=5, intensidade=4, persistencia=4,
        familias=("fruta negra", "herbáceo", "amadeirado", "especiado"),
        servico=Servico(
            "16–18 °C. Mais frio, os taninos endurecem; mais quente, o álcool aparece.",
            "Taça Bordeaux: bojo grande e boca alta.",
            "Jovem (até 5 anos): decante 45–60 min para amaciar. Safra antiga: decante só para separar o depósito.",
        ),
        classicos=("Picanha na brasa", "Cordeiro assado", "Queijos duros curados"),
        poesia="Um rei de capa escura: chega sério, aperta a mão com firmeza e só sorri depois do segundo gole.",
        sinonimos=("cabernet", "cab sauvignon"),
        ficha=(("Tipo", "Tinto"), ("Teor alcoólico típico", "13–14,5%")),
    ),
    Perfil(
        chave="merlot", nome="Merlot", categoria="vinho",
        estilo="Tinto macio",
        origem="Pomerol e Saint-Émilion (França), Serra Gaúcha (Brasil), Chile",
        aromas=("ameixa", "cereja madura", "chocolate ao leite", "ervas secas"),
        ataque="Macio: fruta madura e taninos redondos que abraçam em vez de apertar.",
        retrossabor="Ameixa e chocolate, num final gentil.",
        corpo=3, acidez=3, docura=1, estrutura=3, intensidade=3, persistencia=3,
        familias=("fruta vermelha", "fruta negra", "chocolate", "herbáceo"),
        servico=Servico("15–17 °C.", "Taça Bordeaux.", "Dispensa decantação: 15 minutos na taça já abrem os aromas."),
        classicos=("Massa ao ragu", "Frango assado com ervas", "Risoto de cogumelos"),
        poesia="O abraço de veludo do mundo do vinho: a porta de entrada mais gentil para os tintos.",
        ficha=(("Tipo", "Tinto"), ("Teor alcoólico típico", "13–14%")),
    ),
    Perfil(
        chave="pinot-noir", nome="Pinot Noir", categoria="vinho",
        estilo="Tinto leve e elegante",
        origem="Borgonha (França), Oregon (EUA), Nova Zelândia, Casablanca (Chile)",
        aromas=("cereja", "morango", "framboesa", "cogumelo", "terra molhada"),
        ataque="Leve e vibrante: acidez que faz salivar e taninos quase sussurrados.",
        retrossabor="Frutas vermelhas e chão de floresta (o famoso sous-bois), longo e delicado.",
        corpo=2, acidez=4, docura=1, estrutura=2, intensidade=3, persistencia=4,
        familias=("fruta vermelha", "terroso", "floral"),
        servico=Servico(
            "14–16 °C: um pouco mais fresco que os outros tintos.",
            "Taça Borgonha: bojo largo para os aromas delicados.",
            "Não decante; sirva e deixe abrir na taça.",
        ),
        classicos=("Salmão grelhado", "Magret de pato", "Risoto de cogumelos"),
        poesia="Uma bailarina de seda vermelha: leve nos pés, profunda no olhar.",
        sinonimos=("pinot",),
        ficha=(("Tipo", "Tinto"), ("Teor alcoólico típico", "12,5–14%")),
    ),
    Perfil(
        chave="malbec", nome="Malbec", categoria="vinho",
        estilo="Tinto encorpado e frutado",
        origem="Mendoza (Argentina), Cahors (França)",
        aromas=("amora", "ameixa", "violeta", "baunilha", "cacau"),
        ataque="Fruta escura suculenta, com taninos firmes porém maduros.",
        retrossabor="Violeta e cacau, num final médio a longo.",
        corpo=4, acidez=3, docura=1, estrutura=4, intensidade=4, persistencia=4,
        familias=("fruta negra", "floral", "baunilha", "chocolate"),
        servico=Servico("16–18 °C.", "Taça Bordeaux.", "Decante 20–30 min se for jovem e potente."),
        classicos=("Bife de chorizo", "Costela no fogo de chão", "Empanadas de carne"),
        poesia="Sol de altitude engarrafado: violeta nos aromas e os Andes na alma.",
        ficha=(("Tipo", "Tinto"), ("Teor alcoólico típico", "13,5–14,5%")),
    ),
    Perfil(
        chave="syrah", nome="Syrah / Shiraz", categoria="vinho",
        estilo="Tinto intenso e especiado",
        origem="Vale do Rhône (França), Barossa (Austrália), Vale do São Francisco (PE/BA, Brasil)",
        aromas=("amora", "pimenta-do-reino", "azeitona preta", "defumado", "violeta"),
        ataque="Fruta negra intensa, com um estalo de pimenta no meio da língua.",
        retrossabor="Pimenta, defumado e amora, longo e quente.",
        corpo=4, acidez=3, docura=1, estrutura=4, intensidade=5, persistencia=4,
        familias=("fruta negra", "especiado", "defumado", "floral"),
        servico=Servico("16–18 °C.", "Taça Bordeaux ou de bojo amplo.", "Decante 30 min se for jovem."),
        classicos=("Carne de sol com macaxeira", "Cordeiro com especiarias", "Churrasco"),
        poesia="Pimenta e amora dançando um xaxado: intensidade com ginga nordestina.",
        sinonimos=("syrah", "shiraz"),
        ficha=(("Tipo", "Tinto"), ("Teor alcoólico típico", "13,5–15%")),
        terras=("Nordeste", "Brasil"),
    ),
    Perfil(
        chave="chardonnay", nome="Chardonnay", categoria="vinho",
        estilo="Branco versátil (com ou sem madeira)",
        origem="Borgonha (França), Califórnia (EUA), Serra Gaúcha (Brasil), Chile",
        aromas=("maçã amarela", "abacaxi", "pêssego", "manteiga", "baunilha", "avelã tostada"),
        ataque="Fresco e frutado; quando passa por barrica, chega cremoso e amanteigado.",
        retrossabor="Frutas amarelas e um toque de avelã.",
        corpo=3, acidez=3, docura=1, estrutura=1, intensidade=3, persistencia=3,
        familias=("frutado", "cítrico", "cremoso", "amadeirado", "castanhas"),
        servico=Servico(
            "10–12 °C: tire da geladeira 10 minutos antes.",
            "Taça de branco com bojo médio (a de Borgonha para os de barrica).",
            "Não precisa decantar.",
        ),
        classicos=("Bacalhau ao forno", "Frango ao creme", "Lagosta na manteiga"),
        poesia="O camaleão dourado: cítrico à beira-mar, cremoso à luz de velas.",
        ficha=(("Tipo", "Branco"), ("Teor alcoólico típico", "12,5–14%")),
    ),
    Perfil(
        chave="sauvignon-blanc", nome="Sauvignon Blanc", categoria="vinho",
        estilo="Branco aromático e refrescante",
        origem="Marlborough (Nova Zelândia), Loire (França), Chile",
        aromas=("limão", "maracujá", "grama cortada", "pimentão verde", "giz molhado"),
        ataque="Elétrico: acidez alta e um aroma que pula da taça.",
        retrossabor="Cítrico e herbáceo, limpo e salivante.",
        corpo=2, acidez=5, docura=1, estrutura=1, intensidade=4, persistencia=3,
        familias=("cítrico", "herbáceo", "mineral", "frutado"),
        servico=Servico("7–10 °C.", "Taça de branco com bojo médio.", "Sirva logo; não decante."),
        classicos=("Ceviche", "Queijo de cabra", "Salada verde"),
        poesia="Um mergulho gelado de verão: limão, grama e brisa.",
        sinonimos=("sauvignon",),
        ficha=(("Tipo", "Branco"), ("Teor alcoólico típico", "12–13,5%")),
    ),
    Perfil(
        chave="rose-seco", nome="Rosé Seco", categoria="vinho",
        estilo="Rosé",
        origem="Provence (França), Portugal, Serra Gaúcha (Brasil)",
        aromas=("morango", "framboesa", "pêssego branco", "casca de laranja"),
        ataque="Leve, seco e salivante, com fruta vermelha fresca.",
        retrossabor="Curto e limpo, com um sopro de morango.",
        corpo=2, acidez=4, docura=1, estrutura=1, intensidade=2, persistencia=2,
        familias=("fruta vermelha", "cítrico", "floral", "mineral"),
        servico=Servico("8–10 °C.", "Taça de branco.", "Não decante; mantenha no balde de gelo."),
        classicos=("Camarão grelhado", "Sushi", "Saladas de verão"),
        poesia="O pôr do sol de Boa Viagem servido em taça.",
        sinonimos=("rose", "vinho rose"),
        ficha=(("Tipo", "Rosé"), ("Teor alcoólico típico", "12–13%")),
    ),
    Perfil(
        chave="espumante-brut", nome="Espumante Brut (Método Tradicional)", categoria="vinho",
        estilo="Espumante",
        origem="Champagne (França), Serra Gaúcha (Brasil), Franciacorta (Itália)",
        aromas=("maçã verde", "limão", "brioche", "pão tostado", "amêndoa"),
        ataque="Bolhas finas, acidez viva e quase nenhum açúcar.",
        retrossabor="Pão tostado e cítrico, limpo e refrescante.",
        corpo=2, acidez=5, docura=1, estrutura=1, intensidade=3, persistencia=3,
        familias=("cítrico", "tostado", "mineral", "castanhas"),
        servico=Servico(
            "6–8 °C.",
            "Taça tulipa: guarda as bolhas e mostra os aromas melhor que a flute.",
            "Abra com a garrafa a 45°, segurando a rolha: o som certo é um suspiro, não um tiro.",
        ),
        classicos=("Coxinha e frituras", "Ostras", "Sushi"),
        poesia="Estrelas engarrafadas: cada bolha é uma pequena festa subindo à superfície.",
        sinonimos=("espumante", "espumante brut", "champagne", "champanhe", "brut", "cava"),
        ficha=(("Tipo", "Espumante"), ("Açúcar", "Brut: até 12 g/L na regra europeia"), ("Teor alcoólico típico", "11,5–12,5%")),
        efervescente=True,
    ),
    Perfil(
        chave="espumante-moscatel", nome="Espumante Moscatel", categoria="vinho",
        estilo="Espumante doce",
        origem="Vale do São Francisco (PE/BA, Brasil), Farroupilha (RS, Brasil), Asti (Itália)",
        aromas=("uva fresca", "flor de laranjeira", "pêssego", "mel"),
        ataque="Doce, perfumado e leve, com bolhas delicadas.",
        retrossabor="Uva e flor de laranjeira, curto e alegre.",
        corpo=2, acidez=3, docura=4, estrutura=1, intensidade=4, persistencia=2,
        familias=("frutado", "floral", "mel"),
        servico=Servico("4–6 °C: bem gelado.", "Taça tulipa.", "Abra na hora de servir; depois de aberto, perde as bolhas rápido."),
        classicos=("Bolo de rolo", "Salada de frutas tropicais", "Sobremesas leves"),
        poesia="Uva colhida sob o sol do Sertão, transformada em riso borbulhante.",
        sinonimos=("moscatel", "moscato", "asti"),
        ficha=(("Tipo", "Espumante doce"), ("Teor alcoólico típico", "7–8%")),
        efervescente=True,
        terras=("Nordeste", "Brasil"),
    ),
    Perfil(
        chave="porto-tawny", nome="Vinho do Porto Tawny", categoria="vinho",
        estilo="Fortificado",
        origem="Vale do Douro (Portugal)",
        aromas=("nozes", "figo seco", "caramelo", "casca de laranja cristalizada", "café"),
        ataque="Doce, quente e sedoso, com fruta seca e caramelo.",
        retrossabor="Muito longo: nozes e caramelo que parecem não ter fim.",
        corpo=4, acidez=3, docura=4, estrutura=2, intensidade=4, persistencia=5,
        familias=("caramelo", "castanhas", "frutado", "amadeirado", "chocolate"),
        servico=Servico(
            "12–14 °C: levemente fresco.",
            "Taça pequena de vinho do Porto ou tulipa pequena.",
            "Não decante. Aberto, dura semanas na geladeira.",
        ),
        classicos=("Queijo azul", "Pudim de leite", "Charuto médio"),
        poesia="O tempo em estado líquido: anos de barrica transformando uva em caramelo e memória.",
        sinonimos=("porto", "porto tawny", "tawny", "vinho do porto"),
        ficha=(("Tipo", "Fortificado"), ("Teor alcoólico típico", "19–22%")),
    ),
    Perfil(
        chave="dom-perignon", nome="Dom Pérignon Vintage", categoria="vinho", marca="Moët & Chandon",
        estilo="Champagne safrado (Brut)",
        origem="Champagne (França)",
        aromas=("brioche", "amêndoa", "flor branca", "limão-siciliano", "toque defumado"),
        ataque="Bolhas finíssimas, acidez precisa e textura cremosa.",
        retrossabor="Longo, tostado e mineral.",
        corpo=3, acidez=4, docura=1, estrutura=1, intensidade=4, persistencia=5,
        familias=("tostado", "cítrico", "floral", "mineral", "castanhas"),
        servico=Servico(
            "8–10 °C: um pouco acima dos espumantes jovens, para mostrar a complexidade.",
            "Taça tulipa ou taça de vinho branco.",
            "Abra com calma, a 45°; sirva em duas etapas para a espuma assentar.",
        ),
        classicos=("Ostras", "Lagosta", "Sashimi"),
        poesia="Uma catedral de bolhas: o silêncio luxuoso depois da festa.",
        sinonimos=("dom perignon", "dom perignon vintage"),
        ficha=(("Tipo", "Champagne"), ("Uvas", "Chardonnay e Pinot Noir, só em safras declaradas")),
        efervescente=True,
    ),
    Perfil(
        chave="taylors-10-tawny", nome="Taylor's 10 Anos Tawny", categoria="vinho", marca="Taylor's",
        estilo="Vinho do Porto Tawny 10 anos",
        origem="Vale do Douro (Portugal)",
        aromas=("nozes", "figo", "caramelo", "baunilha", "casca de laranja"),
        ataque="Sedoso e doce, com fruta seca e especiarias.",
        retrossabor="Muito longo, nozes tostadas e caramelo.",
        corpo=4, acidez=3, docura=4, estrutura=2, intensidade=4, persistencia=5,
        familias=("caramelo", "castanhas", "frutado", "amadeirado", "baunilha"),
        servico=Servico("12–14 °C.", "Taça pequena de vinho do Porto.", "Não decante; depois de aberto, guarde na geladeira."),
        classicos=("Queijo azul", "Torta de nozes", "Crème brûlée"),
        poesia="Dez anos de Douro numa taça: paciência que virou doçura.",
        sinonimos=("taylors 10", "taylors tawny", "taylor s 10"),
        ficha=(("Tipo", "Fortificado"), ("Idade média do lote", "10 anos")),
    ),
    # ------------------------------------------------------------- Destilados
    Perfil(
        chave="single-malt-speyside", nome="Whisky Single Malt de Speyside", categoria="destilado",
        estilo="Whisky escocês de malte, sem turfa",
        origem="Speyside (Escócia)",
        aromas=("pera", "maçã", "mel", "baunilha", "flores"),
        ataque="Doce e frutado, com o calor do álcool chegando devagar.",
        retrossabor="Mel e carvalho, médio a longo.",
        corpo=3, acidez=1, docura=3, estrutura=3, intensidade=3, persistencia=4,
        familias=("frutado", "mel", "baunilha", "floral", "amadeirado"),
        servico=Servico(
            WHISKY_TEMPERATURA,
            "Copo Glencairn ou tulipa de degustação.",
            "Gire, cheire de boca entreaberta e beba pouco. Duas ou três gotas de água abrem os aromas.",
        ),
        classicos=("Chocolate ao leite", "Queijo gouda", "Torta de maçã"),
        poesia="Um pomar escocês ao entardecer: pera, mel e madeira contando histórias baixinho.",
        sinonimos=("speyside", "whisky speyside"),
        ficha=(("Teor alcoólico típico", "40–46%"), ("Madeira", "Carvalho, em geral ex-bourbon e ex-Jerez"), ("Idade mínima", "3 anos em barril (lei escocesa)")),
    ),
    Perfil(
        chave="single-malt-islay", nome="Whisky Single Malt de Islay (Turfado)", categoria="destilado",
        estilo="Whisky escocês de malte turfado",
        origem="Ilha de Islay (Escócia)",
        aromas=("fumaça de lareira", "iodo", "maresia", "alcatrão", "mel"),
        ataque="Fumaça e sal logo de saída, com doçura escondida por trás.",
        retrossabor="Muito longo: brasa, maresia e um adocicado de malte.",
        corpo=4, acidez=1, docura=2, estrutura=4, intensidade=5, persistencia=5,
        familias=("defumado", "mineral", "terroso", "mel", "especiado"),
        servico=Servico(
            WHISKY_TEMPERATURA,
            "Copo Glencairn.",
            "Comece com uma gota de água: a turfa recua e a fruta aparece.",
        ),
        classicos=("Ostras", "Salmão defumado", "Queijo azul"),
        poesia="Uma fogueira acesa na beira do Mar do Norte: fumaça, sal e coragem.",
        sinonimos=("islay", "single malt islay", "islay single malt", "whisky turfado", "turfado"),
        ficha=(("Teor alcoólico típico", "43–46%"), ("Turfa", "Malte seco na fumaça de turfa: daí o defumado")),
    ),
    Perfil(
        chave="bourbon", nome="Bourbon", categoria="destilado",
        estilo="Whiskey americano",
        origem="Kentucky (EUA)",
        aromas=("baunilha", "caramelo", "milho doce", "canela", "carvalho tostado"),
        ataque="Doce e redondo: caramelo e baunilha abraçam a língua.",
        retrossabor="Carvalho tostado e especiarias, quente e longo.",
        corpo=4, acidez=1, docura=4, estrutura=4, intensidade=4, persistencia=4,
        familias=("baunilha", "caramelo", "amadeirado", "especiado"),
        servico=Servico(
            "Ambiente, puro; ou sobre uma pedra grande de gelo.",
            "Copo baixo (old fashioned) ou Glencairn.",
            "Para iniciantes: Old Fashioned (bourbon, açúcar, angostura e casca de laranja).",
        ),
        classicos=("Costela barbecue", "Torta de nozes-pecã", "Chocolate amargo"),
        poesia="Caramelo e fogo de chão: o abraço quente do Sul dos Estados Unidos.",
        sinonimos=("bourbon whiskey", "whiskey americano"),
        ficha=(("Teor alcoólico típico", "40–50%"), ("Madeira", "Barril novo de carvalho, queimado por dentro (obrigatório)"), ("Receita", "Pelo menos 51% de milho")),
    ),
    Perfil(
        chave="cachaca-carvalho", nome="Cachaça Envelhecida em Carvalho", categoria="destilado",
        estilo="Cachaça envelhecida",
        origem="Minas Gerais, Paraíba, Pernambuco e todo o Brasil",
        aromas=("cana madura", "baunilha", "coco queimado", "especiarias", "castanhas"),
        ataque="Doce de cana e baunilha, com calor macio.",
        retrossabor="Madeira e especiarias, médio a longo.",
        corpo=3, acidez=1, docura=3, estrutura=3, intensidade=3, persistencia=3,
        familias=("baunilha", "amadeirado", "especiado", "castanhas", "caramelo"),
        servico=Servico(
            "Ambiente (18–20 °C), pura.",
            "Copo tulipa de cachaça ou Glencairn.",
            "Beba aos goles pequenos, como um whisky. Envelhecida é bebida de degustação, não de caipirinha.",
        ),
        classicos=("Feijoada", "Torresmo", "Cocada queimada"),
        poesia="O Brasil colonial descansando em barril: cana que aprendeu a falar baixo.",
        sinonimos=("cachaca envelhecida", "cachaca ouro", "cachaca carvalho", "aguardente envelhecida"),
        ficha=(("Teor alcoólico", "38–48% (lei brasileira)"), ("Madeira", "Carvalho"), ("Envelhecida", "Pelo menos 50% do volume por 1 ano em madeira")),
        terras=("Brasil",),
    ),
    Perfil(
        chave="cachaca-amburana", nome="Cachaça Envelhecida em Amburana", categoria="destilado",
        estilo="Cachaça em madeira brasileira",
        origem="Minas Gerais e alambiques de todo o Brasil",
        aromas=("canela", "cravo", "baunilha", "cumaru", "mel de engenho"),
        ataque="Adocicada e perfumada, como doce de festa junina.",
        retrossabor="Canela e baunilha, longo e quente.",
        corpo=3, acidez=1, docura=4, estrutura=3, intensidade=5, persistencia=4,
        familias=("especiado", "baunilha", "mel", "amadeirado"),
        servico=Servico(
            "Ambiente (18–20 °C), pura.",
            "Copo tulipa de cachaça.",
            "Pura e sem gelo: a amburana é tão perfumada que dispensa coquetel.",
        ),
        classicos=("Queijo coalho com mel de engenho", "Doce de leite", "Cartola"),
        poesia="Uma árvore da Caatinga que perfuma a cana com canela e saudade.",
        sinonimos=("amburana", "umburana", "cachaca amburana", "cachaca umburana"),
        ficha=(("Teor alcoólico", "38–48% (lei brasileira)"), ("Madeira", "Amburana (umburana), nativa do Brasil")),
        terras=("Brasil",),
    ),
    Perfil(
        chave="cachaca-prata", nome="Cachaça Prata", categoria="destilado",
        estilo="Cachaça branca, sem madeira",
        origem="Pernambuco, Minas Gerais, Paraíba e todo o Brasil",
        aromas=("cana-de-açúcar fresca", "capim-limão", "frutas brancas", "ervas"),
        ataque="Seca e viva, com cana fresca no centro.",
        retrossabor="Curto a médio, cana e ervas.",
        corpo=2, acidez=1, docura=1, estrutura=4, intensidade=3, persistencia=2,
        familias=("herbáceo", "cítrico", "frutado"),
        servico=Servico(
            "Fresca (10–14 °C) para degustar; na caipirinha, com muito gelo.",
            "Copinho tulipa ou copo baixo na caipirinha.",
            "Caipirinha: 1 limão-taiti em gomos, 2 colheres de açúcar, 50 ml de cachaça e gelo.",
        ),
        classicos=("Caldinho de feijão", "Petiscos de boteco", "Peixe frito"),
        poesia="A alma da cana em estado puro: o grito alegre do boteco brasileiro.",
        sinonimos=("cachaca prata", "cachaca branca", "cachaca classica", "caipirinha"),
        ficha=(("Teor alcoólico", "38–48% (lei brasileira)"), ("Madeira", "Nenhuma, ou tonel neutro (inox, jequitibá)")),
        terras=("Brasil",),
    ),
    Perfil(
        chave="cognac-vsop", nome="Cognac VSOP", categoria="destilado",
        estilo="Conhaque francês",
        origem="Cognac (França)",
        aromas=("damasco", "uva-passa", "baunilha", "flores secas", "couro"),
        ataque="Sedoso e frutado, com doçura de fruta seca.",
        retrossabor="Baunilha e especiarias, longo e aveludado.",
        corpo=3, acidez=1, docura=3, estrutura=3, intensidade=4, persistencia=4,
        familias=("frutado", "baunilha", "amadeirado", "floral", "couro"),
        servico=Servico(
            "Ambiente (18–20 °C).",
            "Tulipa de degustação ou taça balão pequena.",
            "Segure a taça na palma da mão por um minuto: o calor libera os aromas.",
        ),
        classicos=("Chocolate amargo", "Foie gras", "Charuto médio"),
        poesia="Uvas francesas que dormiram anos em carvalho e acordaram veludo.",
        sinonimos=("cognac", "conhaque", "vsop"),
        ficha=(("Teor alcoólico típico", "40%"), ("Madeira", "Carvalho francês"), ("Idade mínima", "4 anos (VSOP)")),
    ),
    Perfil(
        chave="gin-london-dry", nome="Gin London Dry", categoria="destilado",
        estilo="Gin (destilado de zimbro)",
        origem="Inglaterra e o mundo todo (London Dry é estilo, não endereço)",
        aromas=("zimbro", "casca de limão", "coentro", "pimenta", "raízes"),
        ataque="Seco, resinoso e cítrico.",
        retrossabor="Zimbro e cítrico, médio e limpo.",
        corpo=2, acidez=1, docura=1, estrutura=4, intensidade=4, persistencia=3,
        familias=("herbáceo", "cítrico", "especiado", "floral"),
        servico=Servico(
            "Bem gelado, com muito gelo.",
            "Taça balão.",
            "Gin-tônica: 1 parte de gin para 3 de tônica, gelo até a borda e guarnição que converse com os botânicos.",
        ),
        classicos=("Ceviche", "Camarão grelhado", "Petiscos fritos"),
        poesia="Um jardim inglês destilado: zimbro, limão e frescor de manhã de chuva.",
        sinonimos=("gin", "london dry", "gin tonica"),
        ficha=(("Teor alcoólico típico", "37,5–47%"), ("Madeira", "Nenhuma: o sabor vem dos botânicos, não do barril")),
    ),
    Perfil(
        chave="rum-envelhecido", nome="Rum Envelhecido", categoria="destilado",
        estilo="Rum de melaço envelhecido",
        origem="Cuba, Jamaica, Guatemala, Venezuela",
        aromas=("melaço", "banana caramelizada", "baunilha", "canela", "tabaco"),
        ataque="Doce, redondo e quente.",
        retrossabor="Caramelo e especiarias, longo.",
        corpo=3, acidez=1, docura=4, estrutura=3, intensidade=4, persistencia=4,
        familias=("caramelo", "baunilha", "especiado", "frutado", "amadeirado"),
        servico=Servico(
            "Ambiente, puro ou com uma pedra de gelo.",
            "Copo baixo ou tulipa.",
            "Puro para degustar: é o par clássico de um charuto cubano.",
        ),
        classicos=("Banana flambada", "Pernil com abacaxi", "Charuto cubano"),
        poesia="Caribe ao entardecer: melaço, maresia e uma brisa de tabaco.",
        sinonimos=("rum", "ron", "rhum"),
        ficha=(("Teor alcoólico típico", "40%"), ("Madeira", "Carvalho, muitas vezes ex-bourbon")),
    ),
    Perfil(
        chave="lagavulin-16", nome="Lagavulin 16 Anos", categoria="destilado", marca="Lagavulin",
        estilo="Single Malt de Islay, turfado",
        origem="Islay (Escócia)",
        aromas=("turfa", "iodo", "fumaça de lareira", "frutas secas", "alcaçuz"),
        ataque="Fumaça densa e doçura de frutas secas ao mesmo tempo.",
        retrossabor="Interminável: brasa, sal e alcaçuz.",
        corpo=5, acidez=1, docura=3, estrutura=4, intensidade=5, persistencia=5,
        familias=("defumado", "mineral", "frutado", "especiado"),
        servico=Servico(WHISKY_TEMPERATURA, "Copo Glencairn.", "Prove puro, depois com uma gota de água. Sem pressa."),
        classicos=("Queijo azul", "Chocolate 70%", "Charuto forte"),
        poesia="Um velho faroleiro contando histórias de tempestade ao pé da lareira.",
        sinonimos=("lagavulin", "lagavulin 16"),
        ficha=(("Teor alcoólico", "43%"), ("Idade", "16 anos")),
    ),
    Perfil(
        chave="glenfiddich-12", nome="Glenfiddich 12 Anos", categoria="destilado", marca="Glenfiddich",
        estilo="Single Malt de Speyside",
        origem="Speyside (Escócia)",
        aromas=("pera", "maçã verde", "creme", "carvalho", "malte"),
        ataque="Fresco e frutado, macio para a idade.",
        retrossabor="Médio, carvalho suave e pera.",
        corpo=3, acidez=1, docura=3, estrutura=3, intensidade=3, persistencia=3,
        familias=("frutado", "cremoso", "amadeirado", "baunilha"),
        servico=Servico(WHISKY_TEMPERATURA, "Copo Glencairn.", "Ótimo primeiro single malt: puro, com uma gota de água."),
        classicos=("Queijo gouda", "Torta de maçã", "Salmão defumado"),
        poesia="A porta de entrada do single malt: um pomar fresco com perfume de madeira.",
        sinonimos=("glenfiddich", "glenfiddich 12"),
        ficha=(("Teor alcoólico", "40%"), ("Idade", "12 anos")),
    ),
    Perfil(
        chave="makers-mark", nome="Maker's Mark", categoria="destilado", marca="Maker's Mark",
        estilo="Bourbon de trigo (wheated)",
        origem="Kentucky (EUA)",
        aromas=("caramelo", "baunilha", "pão de trigo", "frutas vermelhas", "carvalho"),
        ataque="Macio e doce, sem a picância do centeio.",
        retrossabor="Caramelo e carvalho, médio a longo.",
        corpo=4, acidez=1, docura=4, estrutura=4, intensidade=4, persistencia=4,
        familias=("caramelo", "baunilha", "amadeirado", "fruta vermelha"),
        servico=Servico("Ambiente, puro ou com uma pedra de gelo.", "Copo baixo.", "Experimente no Old Fashioned ou no Whiskey Sour."),
        classicos=("Costela barbecue", "Torta de nozes-pecã", "Brownie"),
        poesia="O bourbon de cera vermelha: macio como pão de trigo e doce como caramelo.",
        sinonimos=("makers mark", "maker s mark"),
        ficha=(("Teor alcoólico", "45%"), ("Receita", "Milho, trigo vermelho de inverno e cevada maltada; sem centeio")),
    ),
    Perfil(
        chave="pitu", nome="Pitú", categoria="destilado", marca="Pitú",
        estilo="Cachaça clássica, sem madeira",
        origem="Vitória de Santo Antão (PE, Brasil)",
        aromas=("cana-de-açúcar", "ervas", "notas alcoólicas"),
        ataque="Seca e direta, com cana no centro.",
        retrossabor="Curto, cana e calor.",
        corpo=2, acidez=1, docura=1, estrutura=4, intensidade=3, persistencia=2,
        familias=("herbáceo", "cítrico"),
        servico=Servico("Bem gelada, ou na caipirinha.", "Copo baixo.", "Caipirinha de limão-taiti ou batida de maracujá."),
        classicos=("Caldinho de feijão", "Agulhinha frita", "Petiscos de boteco"),
        poesia="O camarão mais famoso do Nordeste: a cachaça de toda roda de amigos pernambucana.",
        sinonimos=("pitu",),
        ficha=(("Origem", "Pernambuco"),),
        terras=("Nordeste", "Brasil"),
    ),
    # --------------------------------------------------------------- Charutos
    Perfil(
        chave="robusto-connecticut", nome="Robusto Dominicano de Capa Connecticut", categoria="charuto",
        estilo="Charuto suave",
        origem="República Dominicana",
        aromas=("creme", "castanhas", "cedro", "pão tostado", "mel"),
        ataque="Fumaça cremosa e adocicada, sem ardor.",
        retrossabor="Castanhas e creme, curto a médio.",
        corpo=2, acidez=1, docura=3, estrutura=2, intensidade=2, persistencia=2,
        familias=("cremoso", "castanhas", "amadeirado", "mel", "tostado"),
        servico=Servico(UMIDOR, "Robusto, cerca de 50 x 127 mm; 45 a 60 minutos de fumo.", CORTE_RETO),
        classicos=("Cappuccino", "Whisky de Speyside", "Café com notas de chocolate"),
        poesia="O primeiro charuto perfeito: uma nuvem de creme e castanhas que não assusta ninguém.",
        sinonimos=("connecticut", "robusto suave", "charuto suave", "robusto dominicano", "dominicano"),
        ficha=(("Bitola", "Robusto · 50 x 127 mm"), ("Capa", "Connecticut Shade, clara e sedosa"), ("Fortaleza", "Suave")),
    ),
    Perfil(
        chave="corona-cubana", nome="Corona Cubana (Habano)", categoria="charuto",
        estilo="Charuto médio",
        origem="Vuelta Abajo, Pinar del Río (Cuba)",
        aromas=("cedro", "terra", "cacau", "café", "especiarias doces"),
        ataque="Terroso e aromático, com doçura de cedro.",
        retrossabor="Cacau e especiarias, médio a longo.",
        corpo=3, acidez=1, docura=2, estrutura=3, intensidade=4, persistencia=4,
        familias=("amadeirado", "terroso", "chocolate", "especiado", "tostado"),
        servico=Servico(UMIDOR, "Corona, cerca de 42 x 142 mm; 40 a 50 minutos.", CORTE_RETO),
        classicos=("Rum envelhecido", "Espresso", "Cognac VSOP"),
        poesia="A elegância de Havana enrolada à mão: cedro, terra e conversa longa.",
        sinonimos=("habano", "corona", "charuto cubano", "cubano", "puro cubano"),
        ficha=(("Bitola", "Corona · 42 x 142 mm"), ("Capa", "Cubana"), ("Fortaleza", "Média")),
    ),
    Perfil(
        chave="toro-maduro-nicaragua", nome="Toro Nicaraguense de Capa Maduro", categoria="charuto",
        estilo="Charuto forte",
        origem="Estelí (Nicarágua)",
        aromas=("chocolate amargo", "café espresso", "pimenta-do-reino", "couro", "terra"),
        ataque="Encorpado e escuro: cacau e pimenta enchem a boca.",
        retrossabor="Muito longo, café e pimenta.",
        corpo=5, acidez=1, docura=3, estrutura=5, intensidade=5, persistencia=5,
        familias=("chocolate", "tostado", "especiado", "couro", "terroso"),
        servico=Servico(
            UMIDOR,
            "Toro, cerca de 52 x 152 mm; 60 a 90 minutos.",
            "Corte reto ou em V; acenda com butano, sem pressa. Fume depois de uma refeição, com água por perto.",
        ),
        classicos=("Bourbon", "Espresso de torra escura", "Porto Tawny"),
        poesia="Uma noite sem lua em Estelí: escura, intensa e cheia de estrelas de pimenta.",
        sinonimos=("maduro", "nicaragua", "charuto forte", "toro maduro", "esteli"),
        ficha=(("Bitola", "Toro · 52 x 152 mm"), ("Capa", "Maduro: folha escura, fermentada por mais tempo"), ("Fortaleza", "Forte")),
    ),
    Perfil(
        chave="baiano-mata-fina", nome="Charuto Baiano de Capa Mata Fina", categoria="charuto",
        estilo="Charuto brasileiro médio",
        origem="Recôncavo Baiano (Bahia, Brasil)",
        aromas=("especiarias doces", "cedro", "terra", "mel de cana", "ervas secas"),
        ataque="Adocicado e especiado, com fumaça macia.",
        retrossabor="Cedro e especiarias, médio.",
        corpo=3, acidez=1, docura=3, estrutura=3, intensidade=3, persistencia=3,
        familias=("especiado", "amadeirado", "terroso", "mel", "herbáceo"),
        servico=Servico(UMIDOR, "Robusto ou Corona são os formatos mais comuns; 45 a 60 minutos.", CORTE_RETO),
        classicos=("Cachaça envelhecida em carvalho", "Café do Cerrado", "Rum envelhecido"),
        poesia="O Recôncavo em fumaça: tabaco que cresceu ouvindo samba de roda.",
        sinonimos=("mata fina", "charuto baiano", "reconcavo", "charuto brasileiro"),
        ficha=(("Capa", "Mata Fina, tabaco escuro do Recôncavo"), ("Fortaleza", "Média")),
        terras=("Nordeste", "Brasil"),
    ),
    Perfil(
        chave="cohiba-siglo-vi", nome="Cohiba Siglo VI", categoria="charuto", marca="Cohiba",
        estilo="Habano médio a forte",
        origem="Cuba",
        aromas=("mel", "cedro", "cacau", "café", "couro"),
        ataque="Cremoso e rico, com doçura de mel.",
        retrossabor="Longo: cacau, couro e cedro.",
        corpo=4, acidez=1, docura=3, estrutura=4, intensidade=4, persistencia=5,
        familias=("mel", "amadeirado", "chocolate", "couro", "cremoso"),
        servico=Servico(UMIDOR, "Cañonazo, 52 x 150 mm; 75 a 90 minutos.", CORTE_RETO),
        classicos=("Cognac", "Rum envelhecido", "Espresso"),
        poesia="Uma sala de leitura em Havana: couro, mel e conversa que atravessa a noite.",
        sinonimos=("siglo vi", "siglo 6", "cohiba siglo 6"),
        ficha=(("Bitola", "Cañonazo · 52 x 150 mm"), ("Fortaleza", "Média a forte")),
    ),
    Perfil(
        chave="montecristo-no2", nome="Montecristo No. 2", categoria="charuto", marca="Montecristo",
        estilo="Habano médio a forte (torpedo)",
        origem="Cuba",
        aromas=("cedro", "cacau", "café", "terra", "especiarias"),
        ataque="Começa médio e cresce: a ponta afinada concentra os sabores.",
        retrossabor="Longo, cacau e madeira.",
        corpo=4, acidez=1, docura=2, estrutura=4, intensidade=4, persistencia=4,
        familias=("amadeirado", "chocolate", "tostado", "terroso", "especiado"),
        servico=Servico(
            UMIDOR,
            "Pirámide (torpedo), 52 x 156 mm; 60 a 90 minutos.",
            "Corte reto só alguns milímetros da ponta afinada: o torpedo concentra a fumaça. Acenda girando, sem pressa.",
        ),
        classicos=("Rum envelhecido", "Espresso", "Porto Tawny"),
        poesia="Um torpedo de cedro e cacau mirando direto na memória.",
        sinonimos=("montecristo 2", "montecristo no 2", "montecristo n 2", "montecristo numero 2"),
        ficha=(("Bitola", "Pirámide · 52 x 156 mm"), ("Fortaleza", "Média a forte")),
    ),
    Perfil(
        chave="partagas-serie-d4", nome="Partagás Serie D No. 4", categoria="charuto", marca="Partagás",
        estilo="Habano forte (robusto)",
        origem="Cuba",
        aromas=("terra", "pimenta", "couro", "cacau", "café"),
        ataque="Encorpado e terroso, com pimenta logo no início.",
        retrossabor="Longo e terroso.",
        corpo=4, acidez=1, docura=2, estrutura=5, intensidade=5, persistencia=5,
        familias=("terroso", "especiado", "couro", "chocolate", "tostado"),
        servico=Servico(UMIDOR, "Robusto, 50 x 124 mm; 50 a 60 minutos.", CORTE_RETO),
        classicos=("Rum envelhecido", "Bourbon", "Espresso"),
        poesia="Força cubana em formato compacto: um soco de veludo terroso.",
        sinonimos=("partagas d4", "partagas serie d", "serie d no 4", "partagas serie d no 4"),
        ficha=(("Bitola", "Robusto · 50 x 124 mm"), ("Fortaleza", "Forte")),
    ),
    Perfil(
        chave="romeo-y-julieta-churchill", nome="Romeo y Julieta Churchill", categoria="charuto", marca="Romeo y Julieta",
        estilo="Habano médio (Churchill)",
        origem="Cuba",
        aromas=("cedro", "flores secas", "café com leite", "castanhas", "couro leve"),
        ataque="Elegante e equilibrado, com fumaça sedosa.",
        retrossabor="Médio a longo, cedro e castanhas.",
        corpo=3, acidez=1, docura=3, estrutura=3, intensidade=3, persistencia=4,
        familias=("amadeirado", "floral", "castanhas", "cremoso", "couro"),
        servico=Servico(UMIDOR, "Churchill (Julieta No. 2), 47 x 178 mm; 75 a 90 minutos.", CORTE_RETO),
        classicos=("Cognac VSOP", "Café do Cerrado", "Porto Tawny"),
        poesia="Um romance em fumaça: longo, elegante e com final feliz.",
        sinonimos=("romeo y julieta", "romeo churchill", "churchill"),
        ficha=(("Bitola", "Churchill (Julieta No. 2) · 47 x 178 mm"), ("Fortaleza", "Média")),
    ),
    # ------------------------------------------------------------------ Cafés
    Perfil(
        chave="cerrado-natural", nome="Cerrado Mineiro Natural", categoria="cafe",
        estilo="Café especial de processo natural",
        origem="Cerrado Mineiro (MG, Brasil)",
        aromas=("chocolate ao leite", "castanhas", "caramelo", "frutas amarelas maduras"),
        ataque="Doce e redondo, com acidez baixa.",
        retrossabor="Chocolate e castanhas, longo e doce.",
        corpo=4, acidez=2, docura=4, estrutura=2, intensidade=3, persistencia=4,
        familias=("chocolate", "castanhas", "caramelo", "frutado"),
        servico=Servico(
            "Água a 92–96 °C, nunca fervendo. Beba entre 55 e 65 °C, quando o doce aparece.",
            "Prensa Francesa ou Espresso.",
            "Prensa: 20 g de café para 300 ml de água (1:15), moagem grossa, 4 minutos e êmbolo descendo devagar.",
        ),
        classicos=("Pão de queijo", "Bolo de rolo", "Chocolate ao leite"),
        poesia="O abraço de chocolate do Brasil: café que parece sobremesa.",
        sinonimos=("cerrado", "cerrado mineiro", "cafe do cerrado", "cafe natural"),
        ficha=(("Processo", "Natural: o fruto seca inteiro e a polpa adoça o grão"), ("Torra indicada", "Média"), ("Pontuação SCA típica", "82–85")),
        terras=("Brasil",),
    ),
    Perfil(
        chave="etiopia-lavado", nome="Etiópia Yirgacheffe Lavado", categoria="cafe",
        estilo="Café especial lavado, floral",
        origem="Yirgacheffe (Etiópia)",
        aromas=("jasmim", "bergamota", "limão-siciliano", "chá preto", "pêssego"),
        ataque="Leve e brilhante, com acidez cítrica.",
        retrossabor="Floral e de chá, limpo e delicado.",
        corpo=2, acidez=5, docura=3, estrutura=1, intensidade=5, persistencia=3,
        familias=("floral", "cítrico", "frutado"),
        servico=Servico(
            "Água a 92–94 °C.",
            "V60 (coado em papel).",
            "V60: 15 g de café para 250 ml (1:16), moagem média-fina, água em espiral; tempo total de 2:30 a 3:00.",
        ),
        classicos=("Torta de limão", "Croissant", "Frutas vermelhas"),
        poesia="Um jardim de jasmins no berço do café: delicado como chá, vivo como limão.",
        sinonimos=("etiopia", "yirgacheffe", "ethiopia", "cafe etiope"),
        ficha=(("Processo", "Lavado: a polpa sai antes da secagem; xícara limpa e ácida"), ("Torra indicada", "Clara"), ("Pontuação SCA típica", "86–89")),
    ),
    Perfil(
        chave="fermentado-anaerobico", nome="Café Fermentado (Anaeróbico)", categoria="cafe",
        estilo="Café especial de fermentação controlada",
        origem="Brasil (Sul de Minas, Mantiqueira), Colômbia, Costa Rica",
        aromas=("vinho tinto", "frutas tropicais maduras", "licor de cereja", "cacau", "canela"),
        ataque="Explosivo: fruta madura e doçura de licor.",
        retrossabor="Longo e vinoso.",
        corpo=3, acidez=4, docura=4, estrutura=1, intensidade=5, persistencia=4,
        familias=("frutado", "fruta vermelha", "chocolate", "especiado"),
        servico=Servico(
            "Água a 90–93 °C.",
            "V60 ou AeroPress.",
            "AeroPress: 15 g para 220 ml, moagem média, 1:30 de infusão e pressão suave por 30 segundos.",
        ),
        classicos=("Chocolate 70%", "Queijo brie", "Charuto médio"),
        poesia="Café que sonhou ser vinho: fruta, licor e ousadia na mesma xícara.",
        sinonimos=("fermentado", "anaerobico", "cafe fermentado", "anaerobic"),
        ficha=(("Processo", "Fermentado: o grão fermenta em tanque sem oxigênio e ganha notas de fruta e vinho"), ("Torra indicada", "Clara a média"), ("Pontuação SCA típica", "86–90")),
        terras=("Brasil",),
    ),
    Perfil(
        chave="mantiqueira-cd", nome="Mantiqueira de Minas Cereja Descascado", categoria="cafe",
        estilo="Café especial semilavado (honey)",
        origem="Mantiqueira de Minas (Sul de MG, Brasil)",
        aromas=("mel", "caramelo", "frutas amarelas", "laranja", "flores"),
        ataque="Doce e equilibrado, com acidez delicada.",
        retrossabor="Mel e caramelo, médio a longo.",
        corpo=3, acidez=3, docura=4, estrutura=2, intensidade=4, persistencia=4,
        familias=("mel", "caramelo", "frutado", "floral", "cítrico"),
        servico=Servico(
            "Água a 92–94 °C.",
            "V60 ou Chemex.",
            "V60: 1:16, moagem média e pré-infusão (bloom) de 30 segundos para o gás sair.",
        ),
        classicos=("Bolo de laranja", "Queijo coalho com mel de engenho", "Croissant"),
        poesia="Mel de altitude em xícara: a doçura das montanhas mineiras.",
        sinonimos=("mantiqueira", "sul de minas", "cereja descascado", "honey"),
        ficha=(("Processo", "Cereja descascado: tira a casca e mantém parte da mucilagem"), ("Torra indicada", "Média-clara"), ("Pontuação SCA típica", "84–88")),
        terras=("Brasil",),
    ),
    Perfil(
        chave="agreste-pernambucano", nome="Café do Agreste Pernambucano (Taquaritinga do Norte)", categoria="cafe",
        estilo="Arábica sombreado de brejo de altitude",
        origem="Taquaritinga do Norte (PE, Brasil)",
        aromas=("rapadura", "frutas amarelas", "chocolate", "flores discretas"),
        ataque="Doce de rapadura e corpo macio.",
        retrossabor="Chocolate e rapadura, médio.",
        corpo=3, acidez=3, docura=4, estrutura=2, intensidade=3, persistencia=3,
        familias=("caramelo", "chocolate", "frutado", "floral"),
        servico=Servico("Água a 92–96 °C.", "V60 ou Prensa Francesa.", "Coado: 1:15, moagem média, servido em xícara pré-aquecida."),
        classicos=("Bolo de rolo", "Cuscuz com manteiga", "Tapioca com queijo coalho"),
        poesia="A serra pernambucana em xícara: café nascido à sombra, com doçura de engenho.",
        sinonimos=("taquaritinga", "agreste", "cafe de pernambuco", "cafe pernambucano"),
        ficha=(("Cultivo", "Sombreado, em brejo de altitude"), ("Torra indicada", "Média"), ("Pontuação SCA", "Varia por lote; peça a ficha do produtor")),
        terras=("Nordeste", "Brasil"),
    ),
    Perfil(
        chave="geisha-panama", nome="Geisha (Panamá)", categoria="cafe",
        estilo="Café especial de variedade rara",
        origem="Boquete (Panamá)",
        aromas=("jasmim", "bergamota", "pêssego", "mel", "mamão"),
        ataque="Sedoso e perfumado, com acidez cítrica elegante.",
        retrossabor="Floral longo, de chá branco.",
        corpo=2, acidez=5, docura=4, estrutura=1, intensidade=5, persistencia=4,
        familias=("floral", "cítrico", "frutado", "mel"),
        servico=Servico("Água a 91–93 °C.", "V60 ou Chemex.", "V60: 1:16, moagem média-fina; beba sem açúcar para não esconder as flores."),
        classicos=("Frutas frescas", "Madeleine", "Panna cotta"),
        poesia="O perfume mais raro do mundo do café: uma gueixa de jasmim e bergamota.",
        sinonimos=("geisha", "gesha", "geisha panama"),
        ficha=(("Processo", "Lavado ou natural"), ("Torra indicada", "Clara"), ("Pontuação SCA típica", "88–94")),
    ),
    Perfil(
        chave="espresso-torra-escura", nome="Blend de Torra Escura (Espresso Italiano)", categoria="cafe",
        estilo="Café de torra escura para espresso",
        origem="Blends de Brasil, América Central e robusta",
        aromas=("chocolate amargo", "tostado", "castanha queimada", "defumado"),
        ataque="Intenso, amargo e encorpado, com crema espessa.",
        retrossabor="Longo, cacau amargo e tostado.",
        corpo=5, acidez=1, docura=2, estrutura=5, intensidade=4, persistencia=4,
        familias=("chocolate", "tostado", "defumado", "castanhas"),
        servico=Servico(
            "Água a 90–92 °C na máquina; xícara pré-aquecida.",
            "Espresso.",
            "Espresso: 18 g de café para 36 g de bebida (1:2) em 25 a 30 segundos.",
        ),
        classicos=("Tiramisù", "Cannoli", "Charuto forte"),
        poesia="Uma noite italiana numa xícara: escura, intensa e sem rodeios.",
        sinonimos=("espresso", "expresso", "torra escura", "cafe italiano", "blend italiano"),
        ficha=(("Processo", "Blend"), ("Torra indicada", "Escura"), ("Pontuação SCA", "Em geral comercial ou gourmet, abaixo de 80")),
    ),
)

# Colunas: chave, nome, momento, peso, gordura, acidez, doçura, picância, sal, famílias, sinônimos.
# maritimo=True para peixes e frutos do mar (taninos firmes deixam gosto metálico).
# terra="Nordeste" ou "Brasil" ativa a regra "o que cresce junto, combina junto" com Perfil.terras.
PRATOS: tuple[Prato, ...] = (
    Prato("picanha", "Picanha na brasa", "principal", 4, 4, 0, 0, 0, 3, ("tostado", "defumado"),
          ("picanha", "churrasco", "carne na brasa", "bife", "fraldinha", "maminha", "carne vermelha", "contrafile", "alcatra")),
    Prato("carne-de-sol", "Carne de sol com macaxeira e manteiga de garrafa", "principal", 4, 4, 0, 1, 0, 4, ("tostado", "cremoso"),
          ("carne de sol", "carne do sol", "macaxeira", "manteiga de garrafa", "charque", "carne seca"), terra="Nordeste"),
    Prato("feijoada", "Feijoada", "principal", 5, 5, 1, 0, 0, 4, ("terroso", "defumado"),
          ("feijoada", "feijao", "tropeiro"), terra="Brasil"),
    Prato("caldinho", "Caldinho de feijão", "petisco", 3, 3, 0, 0, 1, 4, ("terroso", "defumado"),
          ("caldinho", "caldinho de feijao", "caldo de feijao"), terra="Nordeste"),
    Prato("cordeiro", "Cordeiro assado com ervas", "principal", 4, 4, 0, 0, 0, 3, ("herbáceo", "especiado", "tostado"),
          ("cordeiro", "carneiro", "bode", "cabrito", "costeleta de cordeiro")),
    Prato("costela-barbecue", "Costela suína ao barbecue", "principal", 5, 5, 2, 3, 1, 3, ("defumado", "caramelo", "especiado"),
          ("barbecue", "bbq", "costela barbecue", "costelinha", "ribs")),
    Prato("hamburguer", "Hambúrguer artesanal", "principal", 4, 5, 1, 1, 0, 3, ("tostado", "defumado"),
          ("hamburguer", "burger", "x tudo", "sanduiche")),
    Prato("porco-assado", "Pernil ou lombo de porco assado", "principal", 4, 4, 1, 1, 0, 3, ("tostado", "especiado", "herbáceo"),
          ("pernil", "lombo", "porco", "leitao", "carne de porco")),
    Prato("frango-ervas", "Frango assado com ervas", "principal", 3, 2, 1, 0, 0, 2, ("herbáceo", "tostado"),
          ("frango", "galinha", "galeto", "peru", "frango assado")),
    Prato("frango-creme", "Frango ao creme ou estrogonofe", "principal", 3, 4, 1, 1, 0, 2, ("cremoso",),
          ("estrogonofe", "strogonoff", "fricasse", "frango ao creme")),
    Prato("massa-ragu", "Massa ao molho de tomate ou ragu", "principal", 4, 3, 4, 1, 0, 3, ("herbáceo", "terroso"),
          ("macarrao", "massa", "lasanha", "bolonhesa", "ragu", "espaguete", "molho de tomate", "nhoque")),
    Prato("pizza", "Pizza margherita ou calabresa", "principal", 3, 3, 3, 0, 1, 3, ("herbáceo", "tostado"),
          ("pizza",)),
    Prato("risoto-cogumelos", "Risoto de cogumelos", "principal", 3, 3, 1, 0, 0, 2, ("terroso", "cremoso", "castanhas"),
          ("risoto", "cogumelo", "cogumelos", "funghi", "shiitake")),
    Prato("salmao", "Salmão grelhado", "principal", 3, 3, 1, 0, 0, 2, ("cremoso", "terroso"),
          ("salmao", "atum", "truta"), maritimo=True),
    Prato("peixe-branco", "Peixe branco grelhado", "principal", 2, 1, 2, 0, 0, 2, ("cítrico", "mineral", "herbáceo"),
          ("peixe", "pescada", "tilapia", "robalo", "linguado", "dourado", "cioba", "beijupira", "peixe grelhado"), maritimo=True),
    Prato("moqueca", "Moqueca de peixe com dendê e leite de coco", "principal", 3, 4, 2, 1, 2, 3, ("cremoso", "cítrico", "herbáceo", "especiado"),
          ("moqueca", "peixada", "bobo de camarao", "caldeirada"), maritimo=True, terra="Nordeste"),
    Prato("bacalhau", "Bacalhau ao forno com azeite", "principal", 3, 4, 1, 0, 0, 4, ("herbáceo", "mineral"),
          ("bacalhau", "bacalhoada"), maritimo=True),
    Prato("frutos-do-mar", "Camarão e frutos do mar grelhados", "principal", 2, 2, 2, 0, 1, 3, ("mineral", "cítrico"),
          ("camarao", "lagosta", "polvo", "lula", "frutos do mar", "marisco", "siri", "caranguejo"), maritimo=True),
    Prato("sushi", "Sushi e sashimi", "entrada", 1, 2, 2, 1, 1, 3, ("mineral", "cítrico"),
          ("sushi", "sashimi", "temaki", "comida japonesa", "japones", "poke"), maritimo=True),
    Prato("ceviche", "Ceviche", "entrada", 1, 0, 5, 1, 2, 2, ("cítrico", "herbáceo", "mineral"),
          ("ceviche", "tartar de peixe"), maritimo=True),
    Prato("ostras", "Ostras frescas", "entrada", 1, 1, 2, 0, 0, 4, ("mineral",),
          ("ostra", "ostras", "mexilhao", "vongole"), maritimo=True),
    Prato("salada-cabra", "Salada verde com queijo de cabra", "entrada", 1, 2, 3, 0, 0, 2, ("herbáceo", "cítrico", "mineral"),
          ("salada", "queijo de cabra", "folhas")),
    Prato("prato-picante", "Prato apimentado (curry tailandês, acarajé com pimenta)", "principal", 3, 3, 2, 2, 5, 3, ("especiado", "cítrico"),
          ("curry", "apimentado", "picante", "pimenta", "tailandes", "acaraje", "mexicano", "indiano")),
    Prato("frituras", "Coxinha, pastel e frituras de boteco", "petisco", 2, 5, 0, 0, 0, 4, ("tostado",),
          ("coxinha", "pastel", "fritura", "frituras", "bolinho", "petisco", "batata frita", "torresmo", "agulhinha frita")),
    Prato("queijo-azul", "Queijo azul (gorgonzola, roquefort)", "petisco", 4, 5, 1, 0, 0, 5, ("terroso", "cremoso"),
          ("gorgonzola", "roquefort", "queijo azul", "stilton")),
    Prato("queijos-curados", "Tábua de queijos curados (parmesão, canastra)", "petisco", 4, 4, 1, 0, 0, 4, ("castanhas", "tostado"),
          ("parmesao", "canastra", "queijo canastra", "queijos", "tabua de queijos", "queijo curado", "grana padano", "pecorino")),
    Prato("queijo-coalho-mel", "Queijo coalho grelhado com mel de engenho", "petisco", 3, 3, 0, 3, 0, 3, ("tostado", "mel", "caramelo"),
          ("queijo coalho", "coalho", "mel de engenho"), terra="Nordeste"),
    Prato("brie", "Queijo brie ou camembert", "petisco", 3, 5, 1, 0, 0, 3, ("cremoso", "castanhas"),
          ("brie", "camembert")),
    Prato("bolo-de-rolo", "Bolo de rolo com goiabada", "sobremesa", 3, 3, 1, 5, 0, 1, ("frutado", "caramelo"),
          ("bolo de rolo", "goiabada", "romeu e julieta", "bolo"), terra="Nordeste"),
    Prato("cartola", "Cartola (banana, queijo manteiga, açúcar e canela)", "sobremesa", 4, 3, 1, 5, 0, 2, ("caramelo", "especiado", "frutado"),
          ("cartola", "banana frita", "banana caramelizada", "banana flambada"), terra="Nordeste"),
    Prato("pudim", "Pudim de leite", "sobremesa", 3, 3, 0, 5, 0, 1, ("caramelo", "cremoso"),
          ("pudim", "flan", "creme brulee", "doce de leite", "quindim", "sobremesa", "doce")),
    Prato("chocolate-amargo", "Chocolate amargo 70%", "sobremesa", 3, 3, 1, 2, 0, 0, ("chocolate", "tostado"),
          ("chocolate", "chocolate amargo", "chocolate 70", "cacau", "brownie")),
    Prato("tiramisu", "Tiramisù", "sobremesa", 3, 4, 1, 4, 0, 1, ("chocolate", "cremoso", "tostado"),
          ("tiramisu", "pave", "mousse de chocolate")),
    Prato("torta-limao", "Torta de limão", "sobremesa", 2, 3, 4, 4, 0, 1, ("cítrico", "cremoso"),
          ("torta de limao", "mousse de maracuja", "cheesecake")),
    Prato("frutas-tropicais", "Salada de frutas tropicais", "sobremesa", 1, 0, 3, 3, 0, 0, ("frutado", "cítrico", "floral"),
          ("salada de frutas", "frutas", "manga", "abacaxi")),
    Prato("cafe-da-manha", "Pão de queijo, tapioca e cuscuz (café da manhã)", "manha", 2, 3, 0, 1, 0, 3, ("tostado", "cremoso"),
          ("pao de queijo", "tapioca", "cuscuz", "cafe da manha", "croissant", "brunch", "pao na chapa"), terra="Brasil"),
)

PISTAS_ROTULO: tuple[tuple[tuple[str, ...], str], ...] = (
    (("tinto",), "merlot"),
    (("branco",), "chardonnay"),
    (("whisky", "whiskey", "scotch", "malt", "uisque"), "single-malt-speyside"),
    (("cachaca", "pinga", "aguardente"), "cachaca-carvalho"),
    (("brandy",), "cognac-vsop"),
    (("charuto", "cigar", "puro", "robusto"), "corona-cubana"),
    (("cafe", "coffee", "arabica"), "cerrado-natural"),
    (("prosecco",), "espumante-brut"),
)
PISTAS_PRATO: tuple[tuple[tuple[str, ...], str], ...] = (
    (("carne", "boi", "bovino", "file", "cupim", "costela"), "picanha"),
    (("frito", "frita", "fritos"), "frituras"),
    (("queijo",), "queijos-curados"),
    (("torta", "sorvete", "pave"), "pudim"),
    (("vegetariano", "vegano", "legumes"), "risoto-cogumelos"),
)

ROTULO_EIXO = {
    "corpo": "Corpo",
    "acidez": "Acidez",
    "docura": "Doçura",
    "estrutura": "Estrutura",
    "intensidade": "Intensidade",
    "persistencia": "Persistência",
}


def _variantes(*palavras: str) -> tuple[str, ...]:
    formas: list[str] = []
    for palavra in palavras:
        formas.append(palavra)
        if palavra.endswith("o"):
            formas += [palavra[:-1] + "a", palavra + "s", palavra[:-1] + "as"]
        elif palavra.endswith("e"):
            formas.append(palavra + "s")
    return tuple(formas)


def _vocabulario(grupos: dict[tuple[str, ...], object]) -> dict[str, object]:
    return {forma: valor for formas, valor in grupos.items() for forma in formas}


PALADAR_EIXOS: dict[str, object] = _vocabulario({
    _variantes("leve", "delicado", "ligeiro"): ("corpo", 2),
    _variantes("medio", "equilibrado"): ("corpo", 3),
    _variantes("encorpado", "denso", "pesado", "robusto"): ("corpo", 5),
    _variantes("doce"): ("docura", 4),
    _variantes("adocicado"): ("docura", 3),
    _variantes("seco"): ("docura", 1),
    _variantes("acido", "fresco", "refrescante") + ("acidez",): ("acidez", 4),
    _variantes("vibrante", "azedo"): ("acidez", 5),
    _variantes("amargo", "tanico") + ("amargor", "taninos"): ("estrutura", 4),
    _variantes("forte", "potente"): ("estrutura", 5),
    _variantes("suave", "macio", "aveludado", "redondo"): ("estrutura", 2),
    _variantes("intenso", "aromatico", "marcante"): ("intensidade", 5),
    _variantes("discreto"): ("intensidade", 2),
    _variantes("longo", "persistente"): ("persistencia", 5),
})
PALADAR_FAMILIAS: dict[str, object] = _vocabulario({
    ("frutado", "frutada", "frutados", "fruta", "frutas", "frutos"): "frutado",
    ("fruta vermelha", "frutas vermelhas", "morango", "cereja", "framboesa"): "fruta vermelha",
    ("fruta negra", "frutas negras", "frutas escuras", "ameixa", "amora", "cassis"): "fruta negra",
    ("citrico", "citrica", "citricos", "limao", "laranja", "bergamota"): "cítrico",
    ("floral", "florais", "flores", "flor", "jasmim"): "floral",
    ("herbaceo", "ervas", "erva", "zimbro"): "herbáceo",
    ("especiado", "especiada", "especiarias", "canela", "pimenta", "cravo", "apimentado"): "especiado",
    ("amadeirado", "amadeirada", "madeira", "carvalho", "barrica", "cedro"): "amadeirado",
    ("baunilha",): "baunilha",
    ("caramelo", "rapadura", "melaco", "toffee"): "caramelo",
    ("chocolate", "cacau", "chocolatado"): "chocolate",
    ("tostado", "tostada", "torrado", "torrada", "brioche", "pao"): "tostado",
    ("defumado", "defumada", "fumaca", "turfa", "turfado"): "defumado",
    ("terroso", "terrosa", "terra", "cogumelo"): "terroso",
    ("mineral", "salino", "maresia"): "mineral",
    ("castanhas", "castanha", "nozes", "amendoa", "avela"): "castanhas",
    ("mel",): "mel",
    ("cremoso", "cremosa", "amanteigado", "manteiga"): "cremoso",
    ("couro",): "couro",
})
NEGACOES = {"nao", "sem", "pouco", "pouca", "menos", "nada", "nunca", "evito", "odeio", "detesto", "nem"}
INTENSIFICADORES = {"muito", "bem", "super", "bastante", "extremamente"}


# ---------------------------------------------------------------------------
# Utilidades
# ---------------------------------------------------------------------------


def configure_stdio() -> None:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(encoding="utf-8")
        except (OSError, ValueError):
            continue


def arred(valor: float) -> int:
    return int(valor + 0.5)


def limitar(valor: int, minimo: int, maximo: int) -> int:
    return max(minimo, min(maximo, valor))


def normalizar(texto: str) -> str:
    plano = unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode("ascii").lower()
    plano = plano.replace("'", "").replace("`", "")
    return " ".join(re.sub(r"[^a-z0-9]+", " ", plano).split())


def barra(nivel: int) -> str:
    return "●" * nivel + "○" * (5 - nivel)


def veredito(nota: int) -> str:
    if nota >= 85:
        return "Casamento perfeito"
    if nota >= 72:
        return "Ótima companhia"
    if nota >= 58:
        return "Boa parceria"
    return "Arriscado"


def rotulo_eixo(eixo: str, categoria: str) -> str:
    if eixo == "estrutura":
        return ESTRUTURA_NOME[categoria]
    if eixo == "corpo" and categoria == "charuto":
        return "Corpo da fumaça"
    return ROTULO_EIXO[eixo]


def explicar_eixo(eixo: str, nivel: int, categoria: str) -> str:
    indice = limitar(nivel, 1, 5) - 1
    if eixo == "corpo":
        return (CORPO_FUMACA if categoria == "charuto" else CORPO_LIQUIDO)[indice]
    if eixo == "estrutura":
        return ESTRUTURA[categoria][indice]
    tabelas = {"acidez": ACIDEZ, "docura": DOCURA, "intensidade": INTENSIDADE, "persistencia": PERSISTENCIA}
    return tabelas[eixo][indice]


def lista_humana(itens: list[str] | tuple[str, ...]) -> str:
    itens = list(itens)
    if len(itens) <= 1:
        return "".join(itens)
    return ", ".join(itens[:-1]) + " e " + itens[-1]


def resolver_tipo(valor: object) -> str:
    chave = normalizar(str(valor or ""))
    if not chave:
        raise ValueError("Informe o tipo de consulta: " + ", ".join(TIPOS.values()) + ".")
    if chave in TIPOS:
        return chave
    for tipo, nome in TIPOS.items():
        if chave == normalizar(nome):
            return tipo
    for prefixo, tipo in TIPO_PREFIXOS:
        if chave.startswith(prefixo):
            return tipo
    raise ValueError(f"Tipo de consulta desconhecido: {valor}. Use: " + ", ".join(TIPOS.values()) + ".")


def resolver_categoria(valor: object) -> str:
    chave = normalizar(str(valor or ""))
    if not chave or chave in {"todas", "todos", "qualquer"}:
        return ""
    if chave in CATEGORIAS:
        return chave
    if chave in CATEGORIA_ALIASES:
        return CATEGORIA_ALIASES[chave]
    raise ValueError(f"Categoria desconhecida: {valor}. Use: " + ", ".join(CATEGORIAS.values()) + ".")


def localizar(texto: str, itens: list[tuple[object, tuple[str, ...]]],
              prioridade=lambda _obj: 0) -> tuple[object, str, str] | None:
    """Acha o item cujo apelido aparece no texto (confiança alta) ou se parece com ele (média)."""
    alvo = normalizar(texto)
    if not alvo:
        return None
    cercado = f" {alvo} "
    melhor: tuple[int, object, str] | None = None
    for obj, apelidos in itens:
        for apelido in apelidos:
            if apelido and f" {apelido} " in cercado:
                pontos = len(apelido) + prioridade(obj)
                if melhor is None or pontos > melhor[0]:
                    melhor = (pontos, obj, apelido)
    if melhor is not None:
        return melhor[1], "alta", melhor[2]
    tokens = alvo.split()
    parecido: tuple[float, object, str] = (0.0, None, "")
    for obj, apelidos in itens:
        for apelido in apelidos:
            if len(apelido) < 5:
                continue
            tamanho = len(apelido.split())
            janelas = {alvo} | {" ".join(tokens[i:i + tamanho]) for i in range(max(1, len(tokens) - tamanho + 1))}
            for janela in janelas:
                razao = difflib.SequenceMatcher(None, apelido, janela).ratio()
                if razao > parecido[0]:
                    parecido = (razao, obj, apelido)
    if parecido[0] >= 0.84:
        return parecido[1], "média", parecido[2]
    return None


def pista(texto: str, pistas: tuple[tuple[tuple[str, ...], str], ...]) -> str | None:
    tokens = set(normalizar(texto).split())
    for palavras, chave in pistas:
        if tokens & set(palavras):
            return chave
    return None


# ---------------------------------------------------------------------------
# Regras de harmonização (espelham a Seção C do guia)
# ---------------------------------------------------------------------------


def pontuar_prato(perfil: Perfil, prato: Prato) -> Harmonia:
    """Nota de 0 a 100 para a bebida (ou charuto) diante do prato, com os porquês."""
    nota = 62
    motivos: list[str] = []
    cat = perfil.categoria
    doce_com_sal = cat == "vinho" and perfil.docura >= 4 and prato.sal >= 5

    distancia = abs(perfil.peso - prato.peso)
    if distancia == 0:
        nota += 10
        motivos.append(f"Peso com peso: bebida e prato têm a mesma intensidade ({prato.peso}/5).")
    elif distancia == 1:
        nota += 4
        motivos.append("Pesos vizinhos: um não apaga o outro.")
    else:
        nota -= 10 * (distancia - 1) + 4
        quem = "a bebida atropela o prato" if perfil.peso > prato.peso else "o prato cala a bebida"
        motivos.append(f"Desequilíbrio de peso: {quem}.")

    if cat != "charuto":
        if prato.gordura >= 3 and prato.momento != "sobremesa":
            cortes = []
            if perfil.acidez >= 4:
                cortes.append("a acidez")
            if cat == "vinho" and perfil.estrutura >= 4:
                cortes.append("os taninos")
            if perfil.efervescente:
                cortes.append("as bolhas")
            if cat == "destilado" and perfil.estrutura >= 4:
                cortes.append("o álcool")
            if cortes:
                nota += 4 * len(cortes) + 2
                plural = len(cortes) > 1 or cortes[0].startswith(("os ", "as "))
                verbos = "limpam a gordura e preparam" if plural else "limpa a gordura e prepara"
                motivos.append(f"Contraste: {lista_humana(cortes)} {verbos} a próxima garfada.")
            elif prato.gordura >= 4 and cat != "cafe" and not doce_com_sal:
                nota -= 6
                motivos.append("Pouca acidez para tanta gordura: a boca pode ficar pesada.")

        if prato.acidez >= 3 and cat in ("vinho", "cafe"):
            if perfil.acidez >= prato.acidez:
                nota += 5
                motivos.append("Acidez acompanha acidez: o limão ou o tomate do prato não deixam a bebida 'magra'.")
            else:
                nota -= 4 * (prato.acidez - perfil.acidez)
                motivos.append("O prato é mais ácido que a bebida: ela pode parecer aguada.")

        if prato.docura >= 3:
            if perfil.docura >= prato.docura:
                nota += 10
                motivos.append("Doce com doce: a bebida é tão doce quanto o prato, e nada amarga.")
            elif cat == "cafe" and perfil.estrutura >= 3:
                nota += 4
                motivos.append("Contraste: o amargor do café equilibra o açúcar da sobremesa.")
            elif perfil.docura == prato.docura - 1:
                nota += 3
                motivos.append("Doçura quase igual à do prato: funciona, sem sobra.")
            else:
                nota -= 8 * (prato.docura - perfil.docura - 1)
                motivos.append("O prato é bem mais doce que a bebida: ela vai parecer amarga e azeda.")
        elif prato.momento == "sobremesa" and perfil.docura <= 1 and cat != "cafe":
            nota -= 12
            motivos.append("Bebida seca com sobremesa: o açúcar do doce a deixa amarga e ácida.")

        if prato.picancia >= 3:
            if (cat == "vinho" and perfil.estrutura >= 4) or cat == "destilado":
                nota -= 8
                motivos.append("Pimenta com taninos ou álcool alto vira incêndio: o ardor dobra.")
            if perfil.docura >= 2 or (perfil.acidez >= 4 and perfil.estrutura <= 2):
                nota += 6
                motivos.append("Um toque de doçura ou de frescor acalma a pimenta.")

        if doce_com_sal:
            nota += 14
            motivos.append("Sal com doce: o contraste clássico (pense em Porto com gorgonzola).")
        elif cat == "vinho" and perfil.docura >= 4 and prato.docura <= 1:
            nota -= 14
            motivos.append("Vinho doce com prato salgado: o açúcar cansa a boca e apaga o prato.")

        if prato.sal >= 4:
            if perfil.efervescente:
                nota += 5
                motivos.append("Bolhas e sal: a dupla do boteco perfeito.")
            if cat == "vinho" and perfil.estrutura >= 5:
                nota -= 4
                motivos.append("O sal realça o amargor de taninos muito firmes.")

        if prato.maritimo and cat == "vinho" and perfil.estrutura >= 4:
            nota -= 14
            motivos.append("Taninos firmes com peixe ou frutos do mar deixam gosto metálico.")

    pontes = sorted(set(perfil.familias) & set(prato.familias))
    if pontes:
        nota += min(12, 4 * len(pontes))
        verbo = "aparecem" if len(pontes) > 1 else "aparece"
        motivos.append(f"Ponte aromática: {lista_humana(pontes)} {verbo} nos dois lados.")

    if prato.terra and prato.terra in perfil.terras:
        nota += 5
        sotaque = "nordestino" if prato.terra == "Nordeste" else "brasileiro"
        motivos.append(f"O que cresce junto, combina junto: sotaque {sotaque} dos dois lados.")
    elif prato.terra == "Nordeste" and "Brasil" in perfil.terras:
        nota += 3
        motivos.append("O que cresce junto, combina junto: os dois são do Brasil.")

    if cat == "cafe" and prato.momento in ("principal", "entrada"):
        nota -= 15
        motivos.append("O café brilha depois da refeição; para o prato em si, prefira um vinho.")
    if cat == "cafe" and prato.momento == "petisco" and prato.docura <= 1:
        nota -= 10
        motivos.append("Café com petisco salgado não é o par mais feliz; ele pede doce ou o café da manhã.")
    if cat == "cafe" and prato.momento == "manha":
        nota += 18
        motivos.append("Café da manhã é o palco natural do café.")
    if cat in ("destilado", "charuto") and prato.momento == "manha":
        nota -= 20
        motivos.append("Cedo demais para isso: guarde para o fim do dia.")
    if cat == "vinho" and prato.momento == "manha" and not perfil.efervescente:
        nota -= 10
        motivos.append("Vinho de manhã só se for espumante num brunch.")
    if cat == "destilado" and prato.momento == "principal":
        nota -= 10
        motivos.append("À mesa, o vinho é o parceiro natural; o destilado brilha como aperitivo ou digestivo.")
        if perfil.estrutura >= 4:
            nota -= 5
            motivos.append("Destilado potente na refeição: doses pequenas, intercaladas com água.")
    if cat == "charuto":
        motivos.insert(0, "Charuto vem depois da refeição: ele fecha a experiência, não divide o prato.")

    return Harmonia(perfil.chave, perfil.nome, CATEGORIAS[cat], limitar(nota, 0, 100), tuple(motivos))


def intensidade_bebida(perfil: Perfil) -> int:
    estrutura = max(perfil.estrutura, perfil.docura) if perfil.categoria == "vinho" else perfil.estrutura
    return limitar(arred((perfil.corpo + perfil.intensidade + estrutura) / 3), 1, 5)


def pontuar_combo(charuto: Perfil, bebida: Perfil, alvo: Perfil) -> Harmonia:
    """Charuto e bebida (ou café) lado a lado. `alvo` é quem aparece no ranking."""
    nota = 60
    motivos: list[str] = []
    forca = charuto.estrutura
    potencia = intensidade_bebida(bebida)
    distancia = abs(forca - potencia)
    if distancia == 0:
        nota += 12
        motivos.append(f"Força com força: fortaleza do charuto e intensidade da bebida no mesmo nível ({forca}/5).")
    elif distancia == 1:
        nota += 6
        motivos.append("Intensidades vizinhas: os dois se escutam.")
    else:
        nota -= 10 * (distancia - 1) + 4
        quem = "o charuto cala a bebida" if forca > potencia else "a bebida apaga o charuto"
        motivos.append(f"Desequilíbrio: {quem}.")

    pontes = sorted(set(charuto.familias) & set(bebida.familias))
    if pontes:
        nota += min(15, 5 * len(pontes))
        motivos.append(f"Ponte aromática: {lista_humana(pontes)} na fumaça e no copo.")
    if forca >= 3 and bebida.docura >= 3:
        nota += 6 if forca >= 4 else 3
        motivos.append("A doçura da bebida controla o amargor do fumo e arredonda a nicotina.")
    if bebida.categoria == "destilado":
        if bebida.estrutura <= 3:
            nota += 3
            motivos.append("Álcool na medida, por volta de 40%: dá corpo e limpa a boca sem anestesiar "
                           "o paladar (Walter Saes).")
        else:
            nota -= 4
            motivos.append("Álcool acima de uns 40% anestesia as papilas e o charuto some (Walter Saes).")
    if forca <= 2 and bebida.categoria == "destilado" and bebida.estrutura >= 4:
        nota -= 6
        motivos.append("Álcool alto atropela a delicadeza de um charuto suave.")
    if bebida.categoria == "destilado" and bebida.docura <= 1:
        nota -= 8
        motivos.append("Destilado seco e sem madeira: falta doçura para abraçar a fumaça.")
    if bebida.categoria == "vinho":
        if bebida.efervescente and forca <= 2:
            nota += 6
            motivos.append("O perlage limpa o paladar entre uma baforada e outra: espumante é par de "
                           "charuto suave (Walter Saes).")
        elif bebida.efervescente:
            nota -= 10
            motivos.append("Bolhas delicadas somem diante de um charuto médio ou forte; espumante só "
                           "com os suaves (Walter Saes).")
        elif bebida.docura <= 2:
            nota -= 12
            motivos.append("Vinho tranquilo seco acompanha, mas não casa: o charuto amortece a boca e o "
                           "vinho desaparece (Walter Saes).")
    if bebida.categoria == "cafe":
        if bebida.corpo >= 3:
            nota += 3
            motivos.append("Café encorpado: o tostado conversa com a folha curada.")
        if bebida.acidez >= 5:
            nota -= 4
            motivos.append("Acidez muito alta briga com a fumaça; prefira cafés doces e encorpados.")

    return Harmonia(alvo.chave, alvo.nome, CATEGORIAS[alvo.categoria], limitar(nota, 0, 100), tuple(motivos))


def interpretar_paladar(texto: str) -> Preferencias:
    """Transforma 'gosto de doce e frutado, pouco amargo' em alvos de 1 a 5 e famílias."""
    prefs = Preferencias()
    for trecho in re.split(r"[,;.!?\n]+", texto):
        for clausula in re.split(r"\b(?:mas|porem|contudo|entretanto)\b", normalizar(trecho)):
            tokens = clausula.split()
            negacao_desde = next((i for i, t in enumerate(tokens) if t in NEGACOES), len(tokens))
            i = 0
            while i < len(tokens):
                dupla = " ".join(tokens[i:i + 2]) if i + 1 < len(tokens) else ""
                termo, passo = (dupla, 2) if dupla in PALADAR_FAMILIAS else (tokens[i], 1)
                negado = i > negacao_desde
                if termo in PALADAR_FAMILIAS:
                    familia = str(PALADAR_FAMILIAS[termo])
                    if negado:
                        prefs.evita.add(familia)
                        prefs.gosta.discard(familia)
                    else:
                        prefs.gosta.add(familia)
                        prefs.evita.discard(familia)
                    prefs.termos.append(("sem " if negado else "") + termo)
                elif termo in PALADAR_EIXOS:
                    eixo, nivel = PALADAR_EIXOS[termo]  # type: ignore[misc]
                    if i > 0 and tokens[i - 1] in INTENSIFICADORES:
                        nivel = min(5, nivel + 1)
                    if negado:
                        nivel = max(1, 6 - nivel)
                    prefs.alvos[eixo] = nivel
                    prefs.termos.append(("pouco " if negado else "") + termo)
                i += passo
    if not prefs.alvos and not prefs.gosta and not prefs.evita:
        raise ValueError(
            "Não entendi o seu paladar. Use palavras como: leve, encorpado, doce, seco, ácido, suave, forte, "
            "amargo, frutado, floral, chocolate, caramelo, defumado, especiado, madeira."
        )
    return prefs


def pontuar_paladar(perfil: Perfil, prefs: Preferencias) -> Harmonia:
    nota = 92
    motivos: list[str] = []
    for eixo, alvo in prefs.alvos.items():
        if eixo == "acidez" and perfil.categoria in ("destilado", "charuto"):
            continue
        valor = getattr(perfil, eixo)
        distancia = abs(valor - alvo)
        nota -= 9 * distancia
        rotulo = rotulo_eixo(eixo, perfil.categoria)
        if distancia == 0:
            motivos.append(f"{rotulo} {valor}/5: exatamente o que você pediu.")
        elif distancia >= 2:
            motivos.append(f"{rotulo} {valor}/5, longe do pedido ({alvo}/5).")
    amados = sorted(set(perfil.familias) & prefs.gosta)
    if amados:
        nota += 6 * len(amados)
        motivos.append(f"Tem o que você ama: {lista_humana(amados)}.")
    elif prefs.gosta:
        nota -= 10
        motivos.append(f"Não traz {lista_humana(sorted(prefs.gosta))}.")
    evitados = sorted(set(perfil.familias) & prefs.evita)
    if evitados:
        nota -= 15 * len(evitados)
        motivos.append(f"Atenção: tem {lista_humana(evitados)}, que você prefere evitar.")
    return Harmonia(perfil.chave, perfil.nome, CATEGORIAS[perfil.categoria], limitar(nota, 0, 100), tuple(motivos))


# ---------------------------------------------------------------------------
# Leitura de rótulo por imagem (Gemini, opcional)
# ---------------------------------------------------------------------------


def ler_rotulo_por_imagem(caminho: Path) -> tuple[str, str]:
    """Devolve (texto lido, método). A IA só transcreve; o perfil sensorial vem do CATALOGO."""
    if not caminho.is_file():
        raise ValueError(f"Não encontrei a imagem: {caminho}")
    mime = MIME_IMAGEM.get(caminho.suffix.lower())
    if mime is None:
        raise ValueError("Formato de imagem não suportado. Use JPG, PNG, WEBP ou HEIC.")
    chave = os.environ.get("GEMINI_API_KEY", "").strip()
    if not chave:
        texto = " ".join(re.split(r"[_\-.]+", caminho.stem))
        return texto, "nome do arquivo (defina GEMINI_API_KEY para a leitura visual do rótulo)"
    dados = caminho.read_bytes()
    if len(dados) > IMAGEM_MAX_BYTES:
        raise ValueError("Imagem acima de 15 MB. Reduza a foto e tente de novo.")
    modelo = os.environ.get("GEMINI_MODEL", "").strip() or GEMINI_MODELO_PADRAO
    corpo = {
        "contents": [{
            "role": "user",
            "parts": [
                {"text": PROMPT_ROTULO},
                {"inline_data": {"mime_type": mime, "data": base64.b64encode(dados).decode("ascii")}},
            ],
        }],
        "generationConfig": {"temperature": 0},
    }
    pedido = urllib.request.Request(
        f"{GEMINI_URL}/{urllib.parse.quote(modelo)}:generateContent",
        data=json.dumps(corpo).encode("utf-8"),
        headers={"Content-Type": "application/json", "x-goog-api-key": chave},
        method="POST",
    )
    try:
        with urllib.request.urlopen(pedido, timeout=60) as resposta:
            retorno = json.loads(resposta.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        bruto = exc.read().decode("utf-8", "ignore")
        try:
            detalhe = json.loads(bruto)["error"]["message"]
        except (ValueError, KeyError, TypeError):
            detalhe = bruto[:200]
        raise ValueError(f"O Gemini recusou a leitura da imagem (HTTP {exc.code}): {detalhe}") from exc
    except (urllib.error.URLError, TimeoutError) as exc:
        raise ValueError(f"Sem conexão com o Gemini: {exc}") from exc
    candidatos = retorno.get("candidates") or [{}]
    partes = candidatos[0].get("content", {}).get("parts", [])
    texto = " ".join(p.get("text", "") for p in partes if not p.get("thought")).strip()
    if not texto:
        raise ValueError("O Gemini não conseguiu ler o rótulo. Tente uma foto nítida, de frente e com boa luz.")
    return texto, f"imagem lida pelo Gemini ({modelo})"


# ---------------------------------------------------------------------------
# Preços de referência (precos_referencia.csv, preenchido à mão)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Preco:
    chave: str
    loja: str
    produto: str
    valor: float
    data: date
    cidade: str = ""
    link: str = ""

    def dias(self, hoje: date | None = None) -> int:
        return ((hoje or date.today()) - self.data).days

    def conferir(self, hoje: date | None = None) -> bool:
        return self.dias(hoje) > PRECO_VALIDADE_DIAS

    def descrever(self, hoje: date | None = None) -> str:
        loja = f"{self.loja} ({self.cidade})" if self.cidade else self.loja
        texto = f"{self.produto} · {reais(self.valor)} · {loja} · visto em {self.data:%d/%m/%Y}"
        if self.conferir(hoje):
            texto += f" · confira, o preço tem {self.dias(hoje)} dias"
        return texto

    def to_dict(self) -> dict[str, object]:
        return {
            "loja": self.loja,
            "produto": self.produto,
            "preco": self.valor,
            "preco_formatado": reais(self.valor),
            "data": self.data.isoformat(),
            "dias": self.dias(),
            "conferir": self.conferir(),
            "cidade": self.cidade,
            "link": self.link,
        }


def reais(valor: float) -> str:
    return "R$ " + f"{valor:,.2f}".replace(",", "_").replace(".", ",").replace("_", ".")


def ler_valor(texto: str) -> float | None:
    limpo = re.sub(r"[^\d,.]", "", texto)
    if "," in limpo:
        limpo = limpo.replace(".", "").replace(",", ".")
    try:
        valor = float(limpo)
    except ValueError:
        return None
    return valor if valor > 0 else None


def ler_data(texto: str) -> date | None:
    for formato in ("%Y-%m-%d", "%d/%m/%Y"):
        try:
            dia = datetime.strptime(texto.strip(), formato).date()
        except ValueError:
            continue
        return dia if dia <= date.today() else None
    return None


def carregar_precos(caminho: Path, chaves_validas: set[str] | None = None
                    ) -> tuple[dict[str, tuple[Preco, ...]], list[str]]:
    """Lê o CSV (separador ;) e devolve as ofertas por chave do catálogo e os avisos.

    Linhas em branco ou começando com # são ignoradas. Linha com chave, preço
    ou data inválidos vira aviso e fica de fora; o resto da tabela segue valendo.
    """
    if not caminho.is_file():
        return {}, []
    try:
        linhas = caminho.read_text(encoding="utf-8-sig").splitlines()
    except (OSError, UnicodeDecodeError) as exc:
        return {}, [f"Não consegui ler {caminho.name}: {exc}."]
    validas = chaves_validas if chaves_validas is not None else {p.chave for p in CATALOGO}
    cabecalho: list[str] | None = None
    ofertas: dict[str, list[Preco]] = {}
    avisos: list[str] = []
    for numero, linha in enumerate(linhas, 1):
        if not linha.strip() or linha.lstrip().startswith("#"):
            continue
        valores = [v.strip() for v in next(csv.reader([linha], delimiter=";"))]
        if cabecalho is None:
            cabecalho = [normalizar(v) for v in valores]
            faltando = [c for c in PRECO_COLUNAS if c not in cabecalho]
            if faltando:
                return {}, [f"{caminho.name}: faltam as colunas {', '.join(faltando)} no cabeçalho."]
            continue
        dados = dict(zip(cabecalho, valores))
        onde = f"{caminho.name}, linha {numero}"
        chave = dados.get("chave", "").lower()
        valor = ler_valor(dados.get("preco", ""))
        dia = ler_data(dados.get("data", ""))
        if chave not in validas:
            avisos.append(f"{onde}: a chave “{chave}” não existe no catálogo (veja --listar).")
        elif not dados.get("loja") or not dados.get("produto"):
            avisos.append(f"{onde}: preencha a loja e o produto.")
        elif valor is None:
            avisos.append(f"{onde}: preço inválido “{dados.get('preco', '')}” (use 89,90).")
        elif dia is None:
            avisos.append(f"{onde}: data inválida ou futura “{dados.get('data', '')}” (use AAAA-MM-DD ou DD/MM/AAAA).")
        else:
            ofertas.setdefault(chave, []).append(
                Preco(chave, dados["loja"], dados["produto"], valor, dia,
                      dados.get("cidade", ""), dados.get("link", ""))
            )
    tabela = {c: tuple(sorted(lista, key=lambda o: (o.valor, o.produto))) for c, lista in ofertas.items()}
    return tabela, avisos


# ---------------------------------------------------------------------------
# Resultado
# ---------------------------------------------------------------------------


def descrever_perfil(perfil: Perfil) -> dict[str, object]:
    cat = perfil.categoria
    nomes = SERVICO_ROTULOS[cat]
    servico = (perfil.servico.temperatura, perfil.servico.utensilio, perfil.servico.ritual)
    return {
        "chave": perfil.chave,
        "nome": perfil.nome,
        "marca": perfil.marca,
        "categoria": CATEGORIAS[cat],
        "estilo": perfil.estilo,
        "origem": perfil.origem,
        "ficha": dict(perfil.ficha),
        "descricao_didatica": {
            "aromas": list(perfil.aromas),
            "ataque": perfil.ataque,
            "eixos": [
                {
                    "eixo": eixo,
                    "rotulo": rotulo_eixo(eixo, cat),
                    "nivel": getattr(perfil, eixo),
                    "explicacao": explicar_eixo(eixo, getattr(perfil, eixo), cat),
                }
                for eixo in EIXOS_VISIVEIS[cat]
            ],
            "retrossabor": {
                "descricao": perfil.retrossabor,
                "persistencia": perfil.persistencia,
                "explicacao": explicar_eixo("persistencia", perfil.persistencia, cat),
            },
            "poesia": perfil.poesia,
        },
        "dica_servico": {
            chave: {"rotulo": rotulo, "texto": texto}
            for chave, rotulo, texto in zip(("temperatura", "utensilio", "ritual"), nomes, servico)
        },
        "classicos": list(perfil.classicos),
    }


@dataclass
class Degustacao:
    consulta: Consulta
    protagonista: Perfil
    identificacao: Identificacao
    harmonizacao: list[Harmonia]
    titulo_harmonizacao: str
    alternativas: list[Harmonia] = field(default_factory=list)
    prato: Prato | None = None
    preferencias: Preferencias | None = None
    onde_encontrar: list[tuple[str, str, tuple[Preco, ...]]] = field(default_factory=list)
    gerado_em: str = field(default_factory=lambda: datetime.now().astimezone().isoformat(timespec="seconds"))

    def to_dict(self) -> dict[str, object]:
        c = self.consulta
        return {
            "meta": {
                "projeto": PROJETO,
                "engine": "SommelAIEngine",
                "versao": ENGINE_VERSION,
                "autor": AUTOR,
                "lema": LEMA,
                "gerado_em": self.gerado_em,
                "guia": GUIA,
            },
            "consulta": {
                "tipo_consulta": TIPOS[c.tipo],
                "categoria": CATEGORIAS.get(c.categoria, ""),
                "entrada": c.entrada,
                "imagem": str(c.imagem) if c.imagem else "",
            },
            "identificacao": self.identificacao.to_dict(),
            "prato": self.prato.to_dict() if self.prato else None,
            "preferencias": self.preferencias.to_dict() if self.preferencias else None,
            "protagonista": descrever_perfil(self.protagonista),
            "harmonizacao": {
                "titulo": self.titulo_harmonizacao,
                "sugestoes": [h.to_dict() for h in self.harmonizacao],
            },
            "alternativas": [h.to_dict() for h in self.alternativas],
            "onde_encontrar": [
                {"chave": chave, "nome": nome, "ofertas": [o.to_dict() for o in ofertas]}
                for chave, nome, ofertas in self.onde_encontrar
            ],
        }

    def resumo_historico(self) -> dict[str, object]:
        melhor = self.harmonizacao[0] if self.harmonizacao else None
        return {
            "gerado_em": self.gerado_em,
            "tipo_consulta": TIPOS[self.consulta.tipo],
            "categoria": CATEGORIAS.get(self.consulta.categoria, ""),
            "entrada": self.identificacao.texto,
            "protagonista": self.protagonista.nome,
            "melhor_par": melhor.nome if melhor else "",
            "nota": melhor.nota if melhor else None,
        }

    def export(self, destino: Path, manter_historico: bool = True) -> Path:
        dados = self.to_dict()
        anterior = ler_historico(destino) if manter_historico else []
        dados["historico"] = ([self.resumo_historico()] + anterior)[:HISTORICO_MAX]
        destino.parent.mkdir(parents=True, exist_ok=True)
        destino.write_text(json.dumps(dados, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return destino

    def render(self) -> str:
        p = self.protagonista
        c = self.consulta
        regua = "═" * LARGURA
        titulo = f"{PROJETO} · {TIPOS[c.tipo]}"
        if c.categoria:
            titulo += f" · {CATEGORIAS[c.categoria]}"
        linhas = [regua, f"  {titulo}", f"  {LEMA}  ({AUTOR})", regua]
        ident = self.identificacao
        linhas.append(campo("Entrada", ident.texto))
        linhas.append(campo("Leitura", f"{ident.metodo}; confiança {ident.confianca}"))
        if self.prato:
            pr = self.prato
            linhas.append(campo("Prato", f"{pr.nome} (peso {pr.peso}/5, gordura {pr.gordura}/5, "
                                         f"acidez {pr.acidez}/5, doçura {pr.docura}/5, sal {pr.sal}/5)"))
        if self.preferencias:
            linhas.append(campo("Paladar", self.preferencias.resumo(c.categoria)))
        if ident.observacao:
            linhas.append(campo("Nota", ident.observacao))

        linhas += ["", f"◆ {p.nome.upper()}"]
        if p.marca:
            linhas.append(campo("Marca", p.marca))
        linhas.append(campo("Estilo", f"{CATEGORIAS[p.categoria]} · {p.estilo}"))
        linhas.append(campo("Origem", p.origem))
        for rotulo, texto in p.ficha:
            linhas.append(campo(rotulo, texto))
        linhas.append(envolver(f"“{p.poesia}”", "  "))

        linhas += secao("Descrição didática")
        linhas.append(campo("Aromas", lista_humana(p.aromas)))
        linhas.append(campo("Ataque", p.ataque))
        for eixo in EIXOS_VISIVEIS[p.categoria]:
            nivel = getattr(p, eixo)
            linhas.append(campo(rotulo_eixo(eixo, p.categoria), f"{barra(nivel)}  {explicar_eixo(eixo, nivel, p.categoria)}"))
        linhas.append(campo("Retrossabor", f"{barra(p.persistencia)}  {p.retrossabor} "
                                           f"{explicar_eixo('persistencia', p.persistencia, p.categoria)}"))

        linhas += secao("Dica de serviço")
        servico = (p.servico.temperatura, p.servico.utensilio, p.servico.ritual)
        for rotulo, texto in zip(SERVICO_ROTULOS[p.categoria], servico):
            linhas.append(campo(rotulo, texto))

        linhas += secao(self.titulo_harmonizacao)
        for posicao, h in enumerate(self.harmonizacao, 1):
            linhas.append(f"  {posicao}. {h.nome} [{h.categoria}] · {h.nota}/100 · {h.veredito}")
            for motivo in h.motivos:
                linhas.append(envolver(f"· {motivo}", "       "))
        linhas.append(campo("Clássicos", lista_humana(p.classicos)))

        if self.alternativas:
            linhas += secao("Afinidade com o seu paladar")
            for posicao, h in enumerate(self.alternativas, 1):
                linhas.append(f"  {posicao}. {h.nome} [{h.categoria}] · {h.nota}/100")
                for motivo in h.motivos:
                    linhas.append(envolver(f"· {motivo}", "       "))

        if self.onde_encontrar:
            linhas += secao("Onde encontrar · preço de referência")
            for _, nome, ofertas in self.onde_encontrar:
                for posicao, oferta in enumerate(ofertas):
                    linhas.append(campo(nome if posicao == 0 else "", oferta.descrever()))
            linhas.append(envolver("Preços de referência anotados à mão; podem ter mudado. "
                                   "Confirme na loja antes de comprar.", "  "))
        linhas += ["", regua]
        return "\n".join(linhas)


def envolver(texto: str, recuo: str) -> str:
    return textwrap.fill(texto, width=LARGURA, initial_indent=recuo, subsequent_indent=recuo + "  ")


def campo(rotulo: str, texto: str) -> str:
    if not rotulo:
        cabeca = " " * 22
    else:
        cabeca = f"  {rotulo + ':':<20} " if len(rotulo) >= 19 else f"  {rotulo + ':':<20}"
    return textwrap.fill(texto, width=LARGURA, initial_indent=cabeca, subsequent_indent=" " * len(cabeca))


def secao(titulo: str) -> list[str]:
    return ["", f"── {titulo} " + "─" * max(3, LARGURA - len(titulo) - 4)]


def ler_historico(destino: Path) -> list[dict[str, object]]:
    if not destino.is_file():
        return []
    try:
        anterior = json.loads(destino.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []
    historico = anterior.get("historico") if isinstance(anterior, dict) else None
    return [h for h in historico if isinstance(h, dict)] if isinstance(historico, list) else []


# ---------------------------------------------------------------------------
# Motor
# ---------------------------------------------------------------------------


class SommelAIEngine:
    """Sommelier, master blender, cigar sommelier e Q-Grader num só objeto.

    Uso como biblioteca:

        engine = SommelAIEngine()
        resultado = engine.consultar_dict({"tipo_consulta": "paladar", "categoria": "Café",
                                           "entrada": "doce, chocolate, pouco amargo"})

    Sem o argumento precos, lê precos_referencia.csv ao lado do script (se existir).
    """

    def __init__(self, catalogo: tuple[Perfil, ...] = CATALOGO, pratos: tuple[Prato, ...] = PRATOS,
                 top: int = TOP_PADRAO, precos: dict[str, tuple[Preco, ...]] | None = None) -> None:
        self.catalogo = catalogo
        self.pratos = pratos
        self.top = limitar(top, 1, TOP_MAX)
        self.por_chave = {p.chave: p for p in catalogo}
        self.prato_por_chave = {p.chave: p for p in pratos}
        self.avisos_precos: list[str] = []
        if precos is None:
            precos, self.avisos_precos = carregar_precos(DEFAULT_PRECOS, set(self.por_chave))
        self.precos = precos

    # -- API pública ---------------------------------------------------------

    def consultar(self, consulta: Consulta) -> Degustacao:
        acoes = {
            "rotulo": self.analisar_rotulo,
            "prato": self.harmonizar_prato,
            "paladar": self.sugerir_por_paladar,
            "combo": self.combo_vicios_elegantes,
        }
        degustacao = acoes[consulta.tipo](consulta)
        degustacao.onde_encontrar = self.ofertas_para(degustacao)
        return degustacao

    def consultar_dict(self, dados: dict[str, object]) -> dict[str, object]:
        imagem = str(dados.get("imagem") or "").strip()
        consulta = Consulta(
            tipo=resolver_tipo(dados.get("tipo_consulta", DEFAULTS["tipo_consulta"])),
            categoria=resolver_categoria(dados.get("categoria", "")),
            entrada=str(dados.get("entrada") or "").strip(),
            imagem=Path(imagem) if imagem else None,
            top=self.top,
        )
        return self.consultar(consulta).to_dict()

    def analisar_rotulo(self, consulta: Consulta) -> Degustacao:
        perfil, ident = self.identificar_rotulo(consulta)
        if perfil.categoria == "charuto":
            pares, titulo = self.ranking_combo(perfil), "Harmonização recomendada · Combo Vícios Elegantes"
        else:
            pares, titulo = self.ranking_pratos(perfil), "Harmonização recomendada · pratos"
        return Degustacao(consulta, perfil, ident, pares, titulo)

    def harmonizar_prato(self, consulta: Consulta) -> Degustacao:
        prato, ident = self.identificar_prato(consulta.entrada)
        if consulta.categoria:
            candidatos = self.estilos(consulta.categoria)
        else:
            candidatos = [p for p in self.estilos() if p.categoria != "charuto"]
        ranking = sorted((pontuar_prato(p, prato) for p in candidatos), key=lambda h: (-h.nota, h.nome))
        melhor = self.por_chave[ranking[0].chave]
        titulo = f"Harmonização recomendada · {prato.nome}"
        return Degustacao(consulta, melhor, ident, ranking[: self.top], titulo, prato=prato)

    def sugerir_por_paladar(self, consulta: Consulta) -> Degustacao:
        if not consulta.entrada:
            raise ValueError("Descreva o seu paladar em --entrada, por exemplo: 'doce, frutado e pouco amargo'.")
        prefs = interpretar_paladar(consulta.entrada)
        ranking = sorted((pontuar_paladar(p, prefs) for p in self.estilos(consulta.categoria)),
                         key=lambda h: (-h.nota, h.nome))
        melhor = self.por_chave[ranking[0].chave]
        ident = Identificacao(consulta.entrada, "texto", "alta" if ranking[0].nota >= 80 else "média",
                              f"Termos reconhecidos: {', '.join(prefs.termos)}.")
        if melhor.categoria == "charuto":
            pares, titulo = self.ranking_combo(melhor), "Harmonização recomendada · Combo Vícios Elegantes"
        else:
            pares, titulo = self.ranking_pratos(melhor), "Harmonização recomendada · pratos"
        return Degustacao(consulta, melhor, ident, pares, titulo,
                          alternativas=ranking[: self.top], preferencias=prefs)

    def combo_vicios_elegantes(self, consulta: Consulta) -> Degustacao:
        if not consulta.entrada and consulta.imagem is None and consulta.categoria == "charuto":
            consulta = replace(consulta, entrada="charuto suave")
        perfil, ident = self.identificar_rotulo(consulta)
        if perfil.categoria == "charuto":
            pares = self.ranking_combo(perfil)
            titulo = "Combo Vícios Elegantes · bebidas e cafés para este charuto"
        else:
            charutos = self.estilos("charuto")
            pares = sorted((pontuar_combo(ch, perfil, ch) for ch in charutos), key=lambda h: (-h.nota, h.nome))
            pares = pares[: self.top]
            titulo = "Combo Vícios Elegantes · charutos para esta bebida"
        return Degustacao(consulta, perfil, ident, pares, titulo)

    # -- Identificação -------------------------------------------------------

    def estilos(self, categoria: str = "") -> list[Perfil]:
        return [p for p in self.catalogo if not p.e_rotulo and (not categoria or p.categoria == categoria)]

    def identificar_rotulo(self, consulta: Consulta) -> tuple[Perfil, Identificacao]:
        texto, metodo = consulta.entrada, "texto"
        if consulta.imagem is not None:
            texto, metodo = ler_rotulo_por_imagem(consulta.imagem)
        if not texto:
            raise ValueError("Informe o rótulo em --entrada ou uma foto em --imagem.")
        observacoes: list[str] = []
        escopo = [p for p in self.catalogo if not consulta.categoria or p.categoria == consulta.categoria]
        achado = localizar(texto, [(p, self._apelidos(p)) for p in escopo], lambda p: 100 if p.e_rotulo else 0)
        if achado is None and consulta.categoria:
            achado = localizar(texto, [(p, self._apelidos(p)) for p in self.catalogo], lambda p: 100 if p.e_rotulo else 0)
            if achado is not None:
                encontrado = achado[0].categoria  # type: ignore[union-attr]
                observacoes.append(f"Encontrei em {CATEGORIAS[encontrado]}, não em {CATEGORIAS[consulta.categoria]}.")
        if achado is not None:
            perfil, confianca, apelido = achado
            assert isinstance(perfil, Perfil)
            if not perfil.e_rotulo and normalizar(texto) != apelido:
                observacoes.append(
                    f"Rótulo não catalogado: usei o perfil do estilo {perfil.nome}. "
                    "Cadastre-o em CATALOGO para ter notas exclusivas."
                )
            if confianca == "média":
                observacoes.append(f"Li como “{perfil.nome}” por semelhança de escrita.")
            return perfil, Identificacao(texto, metodo, confianca, " ".join(observacoes))
        chave = pista(texto, PISTAS_ROTULO)
        if chave is not None and (not consulta.categoria or self.por_chave[chave].categoria == consulta.categoria):
            perfil = self.por_chave[chave]
            observacoes.append(
                f"Rótulo não catalogado: usei a referência {perfil.nome}. "
                "Informe a uva, o estilo ou a marca para afinar a leitura."
            )
            return perfil, Identificacao(texto, metodo, "baixa", " ".join(observacoes))
        sugestoes = ", ".join(p.nome for p in (self.estilos(consulta.categoria) or self.estilos())[:6])
        raise ValueError(f"Não reconheci o rótulo “{texto}”. Tente a uva, o estilo ou a marca. Exemplos: {sugestoes}.")

    def identificar_prato(self, texto: str) -> tuple[Prato, Identificacao]:
        if not texto:
            raise ValueError("Informe o prato do dia em --entrada.")
        achado = localizar(texto, [(p, self._apelidos(p)) for p in self.pratos])
        if achado is not None:
            prato, confianca, _ = achado
            assert isinstance(prato, Prato)
            nota = f"Li como “{prato.nome}” por semelhança de escrita." if confianca == "média" else ""
            return prato, Identificacao(texto, "texto", confianca, nota)
        chave = pista(texto, PISTAS_PRATO)
        if chave is not None:
            prato = self.prato_por_chave[chave]
            return prato, Identificacao(texto, "texto", "baixa",
                                        f"Prato não catalogado: usei a referência {prato.nome}.")
        exemplos = ", ".join(p.nome for p in self.pratos[:6])
        raise ValueError(f"Não reconheci o prato “{texto}”. Exemplos que conheço: {exemplos}. Veja --listar pratos.")

    @staticmethod
    def _apelidos(item: Perfil | Prato) -> tuple[str, ...]:
        return tuple(dict.fromkeys(normalizar(a) for a in (item.nome, *item.sinonimos) if normalizar(a)))

    # -- Rankings ------------------------------------------------------------

    def ranking_pratos(self, perfil: Perfil) -> list[Harmonia]:
        pontuados = []
        for prato in self.pratos:
            h = pontuar_prato(perfil, prato)
            pontuados.append(Harmonia(prato.chave, prato.nome, "Prato", h.nota, h.motivos))
        return sorted(pontuados, key=lambda h: (-h.nota, h.nome))[: self.top]

    def ranking_combo(self, charuto: Perfil) -> list[Harmonia]:
        bebidas = [
            p for p in self.estilos()
            if p.categoria in ("destilado", "cafe")
            or (p.categoria == "vinho" and (p.docura >= 4 or p.efervescente))
        ]
        ranking = sorted((pontuar_combo(charuto, b, b) for b in bebidas), key=lambda h: (-h.nota, h.nome))
        escolhidos: list[Harmonia] = []
        for categoria in ("Destilado", "Café", "Vinho"):
            melhor = next((h for h in ranking if h.categoria == categoria), None)
            if melhor is not None:
                escolhidos.append(melhor)
        total = max(self.top, len(escolhidos))
        for h in ranking:
            if len(escolhidos) >= total:
                break
            if h not in escolhidos:
                escolhidos.append(h)
        return sorted(escolhidos, key=lambda h: (-h.nota, h.nome))

    # -- Preços --------------------------------------------------------------

    def ofertas_para(self, degustacao: Degustacao) -> list[tuple[str, str, tuple[Preco, ...]]]:
        """Protagonista primeiro, depois as bebidas e charutos sugeridos (pratos não têm preço)."""
        candidatos = [(degustacao.protagonista.chave, degustacao.protagonista.nome)]
        candidatos += [(h.chave, h.nome) for h in (*degustacao.harmonizacao, *degustacao.alternativas)
                       if h.categoria != "Prato"]
        achados: dict[str, tuple[str, str, tuple[Preco, ...]]] = {}
        for chave, nome in candidatos:
            if chave in self.precos and chave not in achados:
                achados[chave] = (chave, nome, self.precos[chave][:OFERTAS_MAX])
        return list(achados.values())


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def listar_catalogo(filtro: str) -> str:
    alvo = normalizar(filtro)
    linhas: list[str] = []
    if alvo in {"pratos", "prato"}:
        linhas.append("Pratos reconhecidos:")
        for p in PRATOS:
            linhas.append(f"  · {p.nome}  [{p.momento}]  apelidos: {', '.join(p.sinonimos[:4])}")
        return "\n".join(linhas)
    categoria = "" if alvo in {"todos", "todas", ""} else resolver_categoria(filtro)
    for chave, nome in CATEGORIAS.items():
        if categoria and chave != categoria:
            continue
        linhas.append(f"{nome}:")
        for p in CATALOGO:
            if p.categoria == chave:
                tipo = f"rótulo · {p.marca}" if p.e_rotulo else "estilo"
                linhas.append(f"  · {p.nome}  ({tipo}; chave {p.chave})")
        linhas.append("")
    if not categoria:
        linhas.append(f"Pratos: {len(PRATOS)} (use --listar pratos).")
    return "\n".join(linhas).rstrip()


def listar_precos(caminho: Path, precos: dict[str, tuple[Preco, ...]], avisos: list[str]) -> str:
    if not precos:
        linhas = [f"Nenhum preço válido em {caminho.name}. Abra o arquivo, siga o exemplo do cabeçalho "
                  "e use as chaves de --listar."]
    else:
        total = sum(len(ofertas) for ofertas in precos.values())
        ofertas_txt = "1 oferta" if total == 1 else f"{total} ofertas"
        itens_txt = "1 item" if len(precos) == 1 else f"{len(precos)} itens"
        linhas = [f"Preços de referência em {caminho.name}: {ofertas_txt} para {itens_txt} do catálogo."]
        nomes = {p.chave: p.nome for p in CATALOGO}
        for chave, ofertas in precos.items():
            linhas.append(f"  · {nomes[chave]}  (chave {chave})")
            linhas += [envolver(oferta.descrever(), "      ") for oferta in ofertas]
    if avisos:
        linhas += ["", "Avisos:"] + [envolver(f"· {aviso}", "  ") for aviso in avisos]
    return "\n".join(linhas)


def read_config_file(path: Path) -> dict[str, object]:
    if not path.is_file():
        raise ValueError(f"Não encontrei o arquivo de configuração: {path}")
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"JSON inválido em {path}: {exc.msg}.") from exc
    if not isinstance(raw, dict):
        raise ValueError("O JSON de configuração precisa ser um objeto.")
    if "consulta" in raw and "meta" in raw:
        consulta = raw["consulta"]
        if not isinstance(consulta, dict):
            raise ValueError("O JSON exportado precisa ter o objeto consulta.")
        return {chave: consulta.get(chave, DEFAULTS[chave]) for chave in DEFAULTS}
    desconhecidas = sorted(set(raw) - ALLOWED_KEYS)
    if desconhecidas:
        raise ValueError(f"Chave desconhecida no JSON: {', '.join(desconhecidas)}.")
    return raw


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=f"{PROJETO} · SommelAIEngine, uma criação de {AUTOR}.",
        epilog=(
            "exemplos:\n"
            '  python sommelai_engine.py --tipo rotulo --entrada "Lagavulin 16"\n'
            '  python sommelai_engine.py --tipo prato --categoria vinho --entrada "carne de sol"\n'
            '  python sommelai_engine.py --tipo paladar --categoria cafe --entrada "doce, chocolate, pouco amargo"\n'
            '  python sommelai_engine.py --tipo combo --entrada "Cohiba Siglo VI"\n'
            "  python sommelai_engine.py --tipo rotulo --imagem fotos/rotulo.jpg\n"
            "  python sommelai_engine.py --config consulta_exemplo.json\n"
            "  python sommelai_engine.py --listar vinho\n"
            "  python sommelai_engine.py --listar precos"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--tipo", dest="tipo_consulta",
                        help="rotulo, prato, paladar ou combo (ou o nome completo). Padrão: Harmonização por Prato.")
    parser.add_argument("--categoria", help="Vinho, Destilado, Charuto ou Café. Vazio = todas.")
    parser.add_argument("--entrada", help="Rótulo, prato do dia ou preferência pessoal, em texto livre.")
    parser.add_argument("--imagem", help="Foto do rótulo (JPG, PNG, WEBP, HEIC). Usa GEMINI_API_KEY se definida.")
    parser.add_argument("--config", type=Path, help="JSON de entrada ou degustacao_resultado.json exportado.")
    parser.add_argument("--output", type=Path, help="Caminho do JSON de saída. Padrão: degustacao_resultado.json.")
    parser.add_argument("--top", type=int, default=TOP_PADRAO, help=f"Quantas sugestões listar (1 a {TOP_MAX}). Padrão: 3.")
    parser.add_argument("--listar", nargs="?", const="todos",
                        help="Lista o catálogo (opcional: vinho, destilado, charuto, cafe, pratos ou precos) e sai.")
    parser.add_argument("--precos", type=Path,
                        help="CSV de preços de referência. Padrão: precos_referencia.csv ao lado do script.")
    parser.add_argument("--json-only", action="store_true", help="Imprime o resultado em JSON e não grava arquivo.")
    parser.add_argument("--sem-historico", action="store_true",
                        help="Grava o JSON sem carregar o histórico anterior.")
    return parser


def load_config(args: argparse.Namespace) -> Consulta:
    data = dict(DEFAULTS)
    if args.config is not None:
        data.update(read_config_file(args.config))
    cli_values = {
        "tipo_consulta": args.tipo_consulta,
        "categoria": args.categoria,
        "entrada": args.entrada,
        "imagem": args.imagem,
    }
    for key, value in cli_values.items():
        if value is not None:
            data[key] = value
    if args.entrada is None and args.imagem is not None:
        data["entrada"] = ""
    if not 1 <= args.top <= TOP_MAX:
        raise ValueError(f"--top precisa ficar entre 1 e {TOP_MAX}.")
    imagem = str(data.get("imagem") or "").strip()
    return Consulta(
        tipo=resolver_tipo(data["tipo_consulta"]),
        categoria=resolver_categoria(data["categoria"]),
        entrada=str(data["entrada"] or "").strip(),
        imagem=Path(imagem) if imagem else None,
        output=args.output if args.output is not None else DEFAULT_OUTPUT,
        json_only=args.json_only,
        top=args.top,
        historico=not args.sem_historico,
    )


def main(argv: list[str] | None = None) -> int:
    configure_stdio()
    try:
        args = build_parser().parse_args(argv)
        caminho_precos = args.precos if args.precos is not None else DEFAULT_PRECOS
        if args.precos is not None and not caminho_precos.is_file():
            raise ValueError(f"Não encontrei o arquivo de preços: {caminho_precos}")
        precos, avisos = carregar_precos(caminho_precos)
        if args.listar is not None:
            if normalizar(args.listar) in {"precos", "preco"}:
                print(listar_precos(caminho_precos, precos, avisos))
            else:
                print(listar_catalogo(args.listar))
            return 0
        for aviso in avisos:
            print(f"Aviso: {aviso}", file=sys.stderr)
        consulta = load_config(args)
        degustacao = SommelAIEngine(top=consulta.top, precos=precos).consultar(consulta)
        if consulta.json_only:
            print(json.dumps(degustacao.to_dict(), ensure_ascii=False))
            return 0
        destino = degustacao.export(consulta.output, consulta.historico)
    except ValueError as exc:
        print(f"Erro: {exc}", file=sys.stderr)
        return 2
    print(degustacao.render())
    print()
    print(f"Arquivo exportado: {destino}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
