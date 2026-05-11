"""
PDF 文档处理模块
使用 PyMuPDF 解析 PDF，语义分块后存入 ChromaDB
"""

import logging
import os
import re
from typing import List, Dict, Any, Optional
from pathlib import Path
try:
    import pymupdf as fitz  # PyMuPDF
except ImportError:
    import fitz  # PyMuPDF
import chromadb
from chromadb.config import Settings
import tiktoken
import redis

from ..config import config
from ..models import get_ali_embeddings
from .sentence_window import SentenceWindowRetriever

logger = logging.getLogger(__name__)


class DocumentChunk:
    """文档块数据结构"""

    def __init__(
        self,
        text: str,
        metadata: Dict[str, Any],
        chunk_id: str
    ):
        self.text = text
        self.metadata = metadata
        self.chunk_id = chunk_id


class SemanticChunker:
    """语义分块器"""

    def __init__(
        self,
        chunk_size: int = 512,
        chunk_overlap: int = 128,
        encoding_name: str = "cl100k_base"
    ):
        self.chunk_size = chunk_size
        self.chunk_overlap = chunk_overlap
        self.tokenizer = tiktoken.get_encoding(encoding_name)

    def count_tokens(self, text: str) -> int:
        """计算文本 token 数"""
        return len(self.tokenizer.encode(text))

    def split_by_sentences(self, text: str) -> List[str]:
        """按句子拆分文本"""
        # 中英文句子分隔符
        sentence_endings = r'[。！？\.!\?]\s*'
        sentences = re.split(sentence_endings, text)
        return [s.strip() for s in sentences if s.strip()]

    def chunk_text(
        self,
        text: str,
        source_file: str,
        page_number: int
    ) -> List[DocumentChunk]:
        """
        语义分块

        策略：
        1. 按句子边界拆分
        2. 累积句子直到达到 chunk_size
        3. 保留 chunk_overlap 的重叠
        """
        sentences = self.split_by_sentences(text)
        chunks = []
        current_chunk = []
        current_tokens = 0
        chunk_index = 0
        char_start = 0

        for sentence in sentences:
            sentence_tokens = self.count_tokens(sentence)

            # 如果单个句子超过 chunk_size，强制拆分
            if sentence_tokens > self.chunk_size:
                if current_chunk:
                    # 保存当前 chunk
                    chunk_text = ' '.join(current_chunk)
                    chunks.append(self._create_chunk(
                        chunk_text, source_file, page_number,
                        chunk_index, char_start, char_start + len(chunk_text)
                    ))
                    chunk_index += 1
                    char_start += len(chunk_text)
                    current_chunk = []
                    current_tokens = 0

                # 强制拆分长句子
                words = sentence.split()
                temp_chunk = []
                temp_tokens = 0

                for word in words:
                    word_tokens = self.count_tokens(word)
                    if temp_tokens + word_tokens > self.chunk_size:
                        if temp_chunk:
                            chunk_text = ' '.join(temp_chunk)
                            chunks.append(self._create_chunk(
                                chunk_text, source_file, page_number,
                                chunk_index, char_start, char_start + len(chunk_text)
                            ))
                            chunk_index += 1
                            char_start += len(chunk_text)
                        temp_chunk = [word]
                        temp_tokens = word_tokens
                    else:
                        temp_chunk.append(word)
                        temp_tokens += word_tokens

                if temp_chunk:
                    current_chunk = temp_chunk
                    current_tokens = temp_tokens
                continue

            # 正常累积句子
            if current_tokens + sentence_tokens > self.chunk_size:
                # 保存当前 chunk
                chunk_text = ' '.join(current_chunk)
                chunks.append(self._create_chunk(
                    chunk_text, source_file, page_number,
                    chunk_index, char_start, char_start + len(chunk_text)
                ))
                chunk_index += 1
                char_start += len(chunk_text)

                # 计算重叠部分
                overlap_chunk = []
                overlap_tokens = 0
                for s in reversed(current_chunk):
                    s_tokens = self.count_tokens(s)
                    if overlap_tokens + s_tokens <= self.chunk_overlap:
                        overlap_chunk.insert(0, s)
                        overlap_tokens += s_tokens
                    else:
                        break

                current_chunk = overlap_chunk + [sentence]
                current_tokens = overlap_tokens + sentence_tokens
            else:
                current_chunk.append(sentence)
                current_tokens += sentence_tokens

        # 保存最后一个 chunk
        if current_chunk:
            chunk_text = ' '.join(current_chunk)
            chunks.append(self._create_chunk(
                chunk_text, source_file, page_number,
                chunk_index, char_start, char_start + len(chunk_text)
            ))

        return chunks

    def _create_chunk(
        self,
        text: str,
        source_file: str,
        page_number: int,
        chunk_index: int,
        char_start: int,
        char_end: int
    ) -> DocumentChunk:
        """创建文档块"""
        chunk_id = f"{Path(source_file).stem}_p{page_number}_c{chunk_index}"
        metadata = {
            'source_file': source_file,
            'page_number': page_number,
            'chunk_index': chunk_index,
            'char_start': char_start,
            'char_end': char_end,
            'token_count': self.count_tokens(text)
        }
        return DocumentChunk(text, metadata, chunk_id)


class PDFDocumentLoader:
    """PDF 文档加载器"""

    def __init__(
        self,
        chroma_client: Optional[chromadb.Client] = None,
        collection_name: str = "documents"
    ):
        self.chunker = SemanticChunker(
            chunk_size=config.CHUNK_SIZE,
            chunk_overlap=config.CHUNK_OVERLAP
        )
        self.embedding_model = get_ali_embeddings()

        # 初始化 ChromaDB
        if chroma_client is None:
            os.makedirs(config.CHROMA_PERSIST_DIR, exist_ok=True)
            self.chroma_client = chromadb.PersistentClient(
                path=config.CHROMA_PERSIST_DIR,
                settings=Settings(anonymized_telemetry=False)
            )
        else:
            self.chroma_client = chroma_client

        self.collection = self.chroma_client.get_or_create_collection(
            name=collection_name,
            metadata={"description": "文档知识库"}
        )

        self.redis_client: Optional[redis.Redis] = None
        try:
            self.redis_client = redis.Redis(
                host=config.REDIS_HOST,
                port=config.REDIS_PORT,
                password=config.REDIS_PASSWORD if config.REDIS_PASSWORD else None,
                db=config.REDIS_DB,
                decode_responses=True,
                socket_connect_timeout=1.5,
                socket_timeout=2.0,
            )
            self.redis_client.ping()
        except Exception as e:
            logger.warning("DocumentLoader Redis 不可用，扩窗上下文将退回进程内缓存: %s", e)
            self.redis_client = None

        self.context_retriever = SentenceWindowRetriever(
            chroma_collection=self.collection,
            redis_client=self.redis_client,
            window_size=config.SENTENCE_WINDOW_SIZE,
        )

        logger.info(f"PDFDocumentLoader 初始化完成，Collection: {collection_name}")

    def extract_text_from_pdf(self, pdf_path: str) -> List[Dict[str, Any]]:
        """
        从 PDF 提取文本

        Returns:
            List of {page_number, text}
        """
        try:
            doc = fitz.open(pdf_path)
            pages = []

            for page_num in range(len(doc)):
                page = doc[page_num]
                text = page.get_text("text")

                # 提取表格（简单处理）
                tables = page.find_tables()
                if tables:
                    for table in tables:
                        extracted = table.extract() or []
                        # PyMuPDF 的表格抽取结果里可能包含 None；这里统一转成空串，避免 join 报错
                        table_text = "\n".join(
                            [
                                " | ".join("" if cell is None else str(cell) for cell in (row or []))
                                for row in extracted
                            ]
                        )
                        text += f"\n\n[表格]\n{table_text}\n"

                pages.append({
                    'page_number': page_num + 1,
                    'text': text.strip()
                })

            doc.close()
            logger.info(f"成功提取 PDF: {pdf_path}，共 {len(pages)} 页")
            return pages

        except Exception as e:
            logger.error(f"提取 PDF 失败 {pdf_path}: {e}")
            raise

    def _index_pages(self, pages: List[Dict[str, Any]], source_path: str) -> int:
        """将已解析的页面文本统一分块、向量化并写入知识库。"""
        all_chunks = []
        page_chunk_map: List[Dict[str, Any]] = []
        for page in pages:
            chunks = self.chunker.chunk_text(
                text=page['text'],
                source_file=source_path,
                page_number=page['page_number']
            )
            all_chunks.extend(chunks)
            page_chunk_map.append({
                'page': page,
                'chunks': chunks,
            })

        if not all_chunks:
            logger.warning(f"文档无有效内容: {source_path}")
            return 0

        texts = [chunk.text for chunk in all_chunks]

        if hasattr(self.embedding_model, "encode"):
            embeddings = self.embedding_model.encode(
                texts,
                show_progress_bar=True
            )
            embeddings_list = embeddings.tolist()
        elif hasattr(self.embedding_model, "embed_documents"):
            embeddings_list = self.embedding_model.embed_documents(texts)
        else:
            raise TypeError(
                f"不支持的 embedding_model 类型: {type(self.embedding_model)}，缺少 encode/embed_documents 方法"
            )

        self.collection.add(
            ids=[chunk.chunk_id for chunk in all_chunks],
            embeddings=embeddings_list,
            documents=texts,
            metadatas=[chunk.metadata for chunk in all_chunks]
        )

        for page_item in page_chunk_map:
            page = page_item['page']
            chunks = page_item['chunks']
            if not chunks:
                continue

            chunk_texts = [chunk.text for chunk in chunks]
            for chunk_position, chunk in enumerate(chunks):
                self.context_retriever.store_full_context(
                    chunk_id=chunk.chunk_id,
                    full_document=page['text'],
                    sentences=chunk_texts,
                    chunk_index=chunk_position,
                    metadata=chunk.metadata,
                )

        logger.info(f"成功索引文档: {source_path}，共 {len(all_chunks)} 个 chunks")
        return len(all_chunks)

    def add_text_document(self, file_path: str) -> int:
        """添加 txt/md 文档到向量库。"""
        try:
            text = Path(file_path).read_text(encoding='utf-8')
            pages = [{
                'page_number': 1,
                'text': text.strip(),
            }]
            return self._index_pages(pages, file_path)
        except UnicodeDecodeError:
            text = Path(file_path).read_text(encoding='utf-8-sig')
            pages = [{
                'page_number': 1,
                'text': text.strip(),
            }]
            return self._index_pages(pages, file_path)
        except Exception as e:
            logger.error(f"添加文本文件失败 {file_path}: {e}")
            raise

    def add_document(self, pdf_path: str) -> int:
        """
        添加单个 PDF 文档到向量库

        Returns:
            添加的 chunk 数量
        """
        try:
            pages = self.extract_text_from_pdf(pdf_path)
            return self._index_pages(pages, pdf_path)

        except Exception as e:
            logger.error(f"添加文档失败 {pdf_path}: {e}")
            raise

    def clear_collection(self) -> int:
        """清空当前 collection 中的所有文档。"""
        existing = self.collection.get()
        ids = existing.get('ids', []) if isinstance(existing, dict) else []
        if not ids:
            return 0

        self.collection.delete(ids=ids)
        return len(ids)

    def load_and_index(self, pdf_directory: str) -> Dict[str, int]:
        """
        批量加载目录下所有 PDF / txt / md

        Returns:
            {filename: chunk_count}
        """
        pdf_dir = Path(pdf_directory)
        if not pdf_dir.exists():
            raise FileNotFoundError(f"目录不存在: {pdf_directory}")

        files = []
        for pattern in ("*.pdf", "*.txt", "*.md"):
            files.extend(pdf_dir.glob(pattern))

        if not files:
            logger.warning(f"目录下无可索引文件: {pdf_directory}")
            return {}

        results = {}
        for file_path in files:
            try:
                suffix = file_path.suffix.lower()
                if suffix == '.pdf':
                    chunk_count = self.add_document(str(file_path))
                else:
                    chunk_count = self.add_text_document(str(file_path))
                results[file_path.name] = chunk_count
            except Exception as e:
                logger.error(f"处理文件失败 {file_path.name}: {e}")
                results[file_path.name] = 0

        logger.info(f"批量索引完成，共处理 {len(results)} 个文件")
        return results

    def get_collection_stats(self) -> Dict[str, Any]:
        """获取 Collection 统计信息"""
        count = self.collection.count()
        return {
            'collection_name': self.collection.name,
            'total_chunks': count,
            'metadata': self.collection.metadata
        }


def _build_arg_parser():
    import argparse

    parser = argparse.ArgumentParser(
        description="Index PDFs into ChromaDB collections (product_manuals/policies/faqs)."
    )
    parser.add_argument(
        "--pdf-dir",
        type=str,
        default=None,
        help="PDF 目录路径（包含 .pdf 文件）。",
    )
    parser.add_argument(
        "--collection",
        type=str,
        default="product_manuals",
        help="ChromaDB collection 名称（建议：product_manuals / policies / faqs）。",
    )
    parser.add_argument(
        "--stats",
        action="store_true",
        help="仅打印 collection 统计信息，不执行入库。",
    )
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    # 允许直接运行 `python src/retrieval/document_loader.py`
    # 也允许以模块方式运行 `python -m src.retrieval.document_loader`
    if __package__ in (None, ""):
        import sys
        from pathlib import Path

        repo_root = str(Path(__file__).resolve().parents[2])
        if repo_root not in sys.path:
            sys.path.insert(0, repo_root)

    logging.basicConfig(
        level=getattr(logging, os.getenv("LOG_LEVEL", "INFO"), logging.INFO),
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )

    parser = _build_arg_parser()
    args = parser.parse_args(argv)

    loader = PDFDocumentLoader(collection_name=args.collection)

    if args.stats:
        print(loader.get_collection_stats())
        return 0

    if not args.pdf_dir:
        parser.error("--pdf-dir is required unless --stats is set")

    results = loader.load_and_index(args.pdf_dir)
    stats = loader.get_collection_stats()
    print({"results": results, "stats": stats})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
