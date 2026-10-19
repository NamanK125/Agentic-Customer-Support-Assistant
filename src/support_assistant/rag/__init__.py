"""Retrieval-augmented product/policy Q&A: knowledge base -> chunks -> embeddings -> agent."""

from support_assistant.rag.agent import RagAgent
from support_assistant.rag.retriever import DenseRetriever, build_retriever

__all__ = ["DenseRetriever", "RagAgent", "build_retriever"]
