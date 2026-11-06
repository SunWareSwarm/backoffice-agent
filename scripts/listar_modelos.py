"""Lista los modelos de chat que tu clave puede usar, para elegir LLM_MODEL y LLM_FALLBACK_MODEL sin adivinar.

    python -m scripts.listar_modelos                 # los del proveedor configurado en el .env
    python -m scripts.listar_modelos --filtro flash  # solo los que contienen «flash»

Pega en el .env el nombre tal como aparece aquí (sin el prefijo «models/»).
Ojo: que un modelo aparezca en la lista no garantiza que esté en tu plan gratuito ni que no esté saturado en este momento.
La prueba real es `python -m scripts.verificar_entorno`. Tus límites exactos se ven en https://aistudio.google.com/rate-limit
"""
import argparse

from app.config import Settings, get_settings


def nombres_google(modelos) -> list[str]:
    """De la lista del SDK de Google, solo los que sirven para generar texto, con el nombre listo para el .env."""
    out = []
    for m in modelos:
        acciones = getattr(m, "supported_actions", None)
        nombre = (getattr(m, "name", "") or "").removeprefix("models/")
        if nombre and (acciones is None or "generateContent" in acciones):
            out.append(nombre)
    return sorted(set(out))


def listar(s: Settings) -> list[str]:
    if s.llm_provider == "google":
        from google import genai

        return nombres_google(genai.Client(api_key=s.google_api_key).models.list())
    if s.llm_provider == "openai":
        from openai import OpenAI

        return sorted(m.id for m in OpenAI(api_key=s.openai_api_key).models.list())
    from anthropic import Anthropic

    return sorted(m.id for m in Anthropic(api_key=s.anthropic_api_key).models.list())


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--filtro", default="", help="texto que debe contener el nombre")
    args = ap.parse_args()
    s = get_settings()
    nombres = [n for n in listar(s) if args.filtro.lower() in n.lower()]
    print(f"Modelos de {s.llm_provider} disponibles para tu clave ({len(nombres)}):")
    for n in nombres:
        print(f"  {n}" + ("   <- el que usas" if n == s.llm_model else "") + ("   <- el de respaldo" if n == s.llm_fallback_model else ""))


if __name__ == "__main__":
    main()
