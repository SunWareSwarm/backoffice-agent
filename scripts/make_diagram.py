"""Genera docs/grafo.mmd (Mermaid) directamente desde el grafo compilado: el diagrama no puede quedar desactualizado.

    python -m scripts.make_diagram
"""
from pathlib import Path

from app.agents.comun import Dependencias
from app.clients import ModelConfig
from app.graph import construir_grafo


def main() -> None:
    deps = Dependencias(ruta_db="", chat=None, texto=None, config_texto=ModelConfig(model_name="diagrama"))
    mermaid = construir_grafo(deps).get_graph().draw_mermaid()
    Path("docs/grafo.mmd").write_text(mermaid, encoding="utf-8")
    print(mermaid)


if __name__ == "__main__":
    main()
