from mandate.config import Settings
from mandate.intent_agent import IntentAgent
from mandate.wiring import build_intent_agents, build_risk_agent


def make_settings(models: tuple[str, ...] = ("llama3.1",), embedder: str = "hashing") -> Settings:
    return Settings(
        paypal_base_url="",
        paypal_client_id="",
        paypal_client_secret="",
        payment_provider="fake",
        private_key_b64="",
        public_key_b64="",
        council_enabled=True,
        ollama_url="http://localhost:11434",
        ollama_model="llama3.1",
        intent_models=models,
        llm_timeout=5.0,
        risk_enabled=True,
        embedder=embedder,
        embedding_model="sentence-transformers/all-MiniLM-L6-v2",
    )


def test_single_model_keeps_default_agent_name() -> None:
    agents = build_intent_agents(make_settings(("llama3.1",)))
    assert [agent.name for agent in agents] == ["intent"]
    assert isinstance(agents[0], IntentAgent)


def test_multiple_models_get_unique_agent_names() -> None:
    agents = build_intent_agents(make_settings(("llama3.1", "qwen2.5:7b-instruct")))
    assert [agent.name for agent in agents] == ["intent-llama3.1", "intent-qwen2.5-7b-instruct"]


def test_risk_agent_builds_with_hashing_embedder() -> None:
    agent = build_risk_agent(make_settings())
    assert agent.name == "risk"
