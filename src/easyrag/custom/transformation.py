import json
import os.path
from typing import Sequence

from llama_index.core.extractors.interface import BaseExtractor
from llama_index.core.schema import BaseNode


class CustomFilePathExtractor(BaseExtractor):
    last_path_length: int = 4
    data_path: str

    def __init__(self, last_path_length: int = 4, data_path: str = "", **kwargs):
        super().__init__(
            last_path_length=last_path_length,
            data_path=data_path,
            **kwargs
        )

    @classmethod
    def class_name(cls) -> str:
        return "CustomFilePathExtractor"

    async def aextract(self, nodes: Sequence[BaseNode]) -> list[dict]:
        pathmap_file = os.path.join(self.data_path, "pathmap.json")
        if os.path.exists(pathmap_file):
            with open(pathmap_file, encoding="utf-8") as f:
                pathmap = json.loads(f.read())
        else:
            pathmap = None
        metadata_list = []
        for node in nodes:
            node.metadata["file_abs_path"] = node.metadata['file_path']
            file_path = os.path.relpath(node.metadata["file_path"], self.data_path)
            file_path = file_path.replace(os.sep, "/")
            node.metadata["dir"] = file_path.split("/", 1)[0]
            node.metadata["file_path"] = file_path
            if pathmap is not None:
                node.metadata["know_path"] = "/".join(pathmap[file_path])
            metadata_list.append(node.metadata)
        return metadata_list


class CustomTitleExtractor(BaseExtractor):
    def __init__(self, *args, **kwargs):
        super().__init__(**kwargs)

    @classmethod
    def class_name(cls) -> str:
        return "CustomTitleExtractor"

    # 将Document的第一行作为标题
    async def aextract(self, nodes: Sequence[BaseNode]) -> list[dict]:
        try:
            document_title = nodes[0].text.split("\n")[0]
            last_file_path = nodes[0].metadata["file_path"]
        except:
            document_title = ""
            last_file_path = ""
        metadata_list = []
        for node in nodes:
            if node.metadata["file_path"] != last_file_path:
                document_title = node.text.split("\n")[0]
                last_file_path = node.metadata["file_path"]
            node.metadata["document_title"] = document_title
            metadata_list.append(node.metadata)

        return metadata_list
