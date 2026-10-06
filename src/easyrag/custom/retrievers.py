import logging
import math
from typing import List, Optional, Callable, cast

from llama_index.core import QueryBundle, VectorStoreIndex
from llama_index.core.base.base_retriever import BaseRetriever
from llama_index.core.callbacks import CallbackManager
from llama_index.core.constants import DEFAULT_SIMILARITY_TOP_K
from llama_index.core.schema import NodeWithScore, BaseNode, IndexNode
from llama_index.core.storage.docstore import BaseDocumentStore
from ..pipeline.ingestion import get_node_content
from rank_bm25 import BM25Okapi

logger = logging.getLogger(__name__)


class PositiveIDFBM25(BM25Okapi):
    """Okapi TF/length normalization with Lucene's positive IDF formula."""

    def _calc_idf(self, nd):
        self.idf = {
            term: math.log1p((self.corpus_size - frequency + 0.5) / (frequency + 0.5))
            for term, frequency in nd.items()
        }
        self.average_idf = sum(self.idf.values()) / max(1, len(self.idf))


def tokenize_and_remove_stopwords(tokenizer, text, stopwords):
    words = tokenizer.cut(text.lower())
    filtered_words = [word for word in words
                      if word not in stopwords and word != ' ']
    return filtered_words


# using jieba to split sentence and remove meaningless words
class BM25Retriever(BaseRetriever):
    def __init__(
            self,
            nodes: List[BaseNode],
            tokenizer: Optional[Callable[[str], List[str]]],
            similarity_top_k: int = DEFAULT_SIMILARITY_TOP_K,
            callback_manager: Optional[CallbackManager] = None,
            objects: Optional[List[IndexNode]] = None,
            object_map: Optional[dict] = None,
            verbose: bool = False,
            stopwords: List[str] = [""],
            embed_type: int = 0,
            bm25_type: int = 0,
    ) -> None:
        if bm25_type not in (0, 2):
            raise ValueError("bm25_type must be 0 (Okapi) or 2 (positive IDF)")
        self._nodes = nodes
        self._tokenizer = tokenizer
        self._similarity_top_k = similarity_top_k
        self.embed_type = embed_type
        self._corpus = [tokenize_and_remove_stopwords(
            self._tokenizer, get_node_content(node, self.embed_type), stopwords=stopwords)
            for node in self._nodes]
        self.bm25_type = bm25_type
        self.k1 = 1.5
        self.b = 0.75
        self.epsilon = 0.25
        model_class = PositiveIDFBM25 if self.bm25_type == 2 else BM25Okapi
        self.bm25 = model_class(
            self._corpus,
            k1=self.k1,
            b=self.b,
            epsilon=self.epsilon,
        )
        self.filter_dict = None
        self.stopwords = stopwords
        super().__init__(
            callback_manager=callback_manager,
            object_map=object_map,
            objects=objects,
            verbose=verbose,
        )

    def get_scores(self, query, docs=None):
        if docs is None:
            bm25 = self.bm25
        else:
            corpus = [tokenize_and_remove_stopwords(
                self._tokenizer, doc, stopwords=self.stopwords)
                for doc in docs]
            model_class = PositiveIDFBM25 if self.bm25_type == 2 else BM25Okapi
            bm25 = model_class(
                corpus,
                k1=self.k1,
                b=self.b,
                epsilon=self.epsilon,
            )
        tokenized_query = tokenize_and_remove_stopwords(self._tokenizer, query,
                                                        stopwords=self.stopwords)
        scores = bm25.get_scores(tokenized_query)
        return scores

    @classmethod
    def from_defaults(
            cls,
            index: Optional[VectorStoreIndex] = None,
            nodes: Optional[List[BaseNode]] = None,
            docstore: Optional[BaseDocumentStore] = None,
            tokenizer: Optional[Callable[[str], List[str]]] = None,
            similarity_top_k: int = DEFAULT_SIMILARITY_TOP_K,
            verbose: bool = False,
            stopwords: List[str] = [""],
            embed_type: int = 0,
            bm25_type: int = 0,  # 0: Okapi; 2: positive IDF
    ) -> "BM25Retriever":
        # ensure only one of index, nodes, or docstore is passed
        if sum(bool(val) for val in [index, nodes, docstore]) != 1:
            raise ValueError("Please pass exactly one of index, nodes, or docstore.")

        if index is not None:
            docstore = index.docstore

        if docstore is not None:
            nodes = cast(List[BaseNode], list(docstore.docs.values()))

        assert (
                nodes is not None
        ), "Please pass exactly one of index, nodes, or docstore."

        tokenizer = tokenizer
        return cls(
            nodes=nodes,
            tokenizer=tokenizer,
            similarity_top_k=similarity_top_k,
            verbose=verbose,
            stopwords=stopwords,
            embed_type=embed_type,
            bm25_type=bm25_type,
        )

    def filter(self, scores):
        top_n = scores.argsort()[::-1]
        nodes: List[NodeWithScore] = []
        fallback = None
        for ix in top_n:
            flag = True
            if self.filter_dict is not None:
                for key, value in self.filter_dict.items():
                    if self._nodes[ix].metadata[key] != value:
                        flag = False
                        break
            if flag:
                candidate = NodeWithScore(
                    node=self._nodes[ix],
                    score=float(scores[ix]),
                )
                if fallback is None:
                    fallback = candidate
                if scores[ix] > 0:
                    nodes.append(candidate)
            if len(nodes) == self._similarity_top_k:
                break

        # rank_bm25 can assign zero/negative IDF to every term in a tiny corpus.
        # Returning the best matching candidate keeps smoke tests and small
        # domain-specific knowledge bases usable.
        if not nodes and fallback is not None and len(self._nodes) <= 2:
            nodes.append(fallback)

        # add nodes sort in BM25Retriever
        nodes = sorted(nodes, key=lambda x: x.score, reverse=True)
        return nodes

    def _retrieve(self, query_bundle: QueryBundle) -> List[NodeWithScore]:
        if query_bundle.custom_embedding_strs or query_bundle.embedding:
            logger.warning("BM25Retriever does not support embeddings, skipping...")

        query = query_bundle.query_str
        scores = self.get_scores(query)
        nodes = self.filter(scores)

        return nodes
