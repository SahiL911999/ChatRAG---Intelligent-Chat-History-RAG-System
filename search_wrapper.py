import os
import re
from typing import Optional, List, Dict, Any
from dotenv import load_dotenv

from langchain_pinecone import PineconeVectorStore
from langchain_google_genai import GoogleGenerativeAIEmbeddings
from langchain_groq import ChatGroq
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import StrOutputParser
from langchain_core.documents import Document

# Load environment variables once
load_dotenv()

class RAGCitationEngine:
    def __init__(self):
        """
        Initializes the RAG Engine components (LLM, Vector Store, Embeddings).
        """
        self.index_name = os.getenv("PINECONE_INDEX")
        
        # 1. Setup Embeddings
        self.embeddings = GoogleGenerativeAIEmbeddings(model="models/text-embedding-004")
        
        # 2. Setup Vector Store
        self.vector_store = PineconeVectorStore.from_existing_index(
            index_name=self.index_name,
            embedding=self.embeddings,
        )
        
        # 3. Setup LLM
        self.llm = ChatGroq(
            model="openai/gpt-oss-120b", # Ensure this matches your Groq model access
            temperature=0
        )
        
        # 4. Setup Prompt Template
        self.template = """
        You are a helpful AI assistant. 
        Answer the user's question based ONLY on the following context sources. 
        Each source has an ID like [1], [2].

        When you use information from a source, you MUST cite it using its ID at the end of the sentence (e.g. "Windows settings can be reset [1].").

        If the answer is not in the context, say "I don't have enough information to answer that."

        Context:
        {context}

        Question: 
        {question}
        """
        self.prompt = ChatPromptTemplate.from_template(self.template)
        
        # 5. Compile the Chain (Prompt -> LLM -> String Output)
        self.chain = self.prompt | self.llm | StrOutputParser()

    def _get_retrieved_docs(self, chat_user: Optional[str], query: str, k: int = 10) -> List[Document]:
        """
        Private Helper: Fetches raw documents from Pinecone.
        """
        pinecone_filter = {}
        if chat_user:
            pinecone_filter = {"chat_user": {"$eq": chat_user}}

        return self.vector_store.similarity_search(
            query,
            k=k,
            filter=pinecone_filter
        )

    def _format_docs_with_ids(self, docs: List[Document]) -> str:
        """
        Private Helper: Formats docs into "Source [1]: Text..." string.
        """
        formatted_string = ""
        for i, doc in enumerate(docs):
            formatted_string += f"Source [{i+1}]:\n{doc.page_content}\n\n"
        return formatted_string

    def _extract_references(self, response_text: str, docs: List[Document]) -> List[Dict]:
        """
        Private Helper: Maps [1] or 【1】 citations back to metadata.
        """
        # Regex matches both standard [1] and fancy 【1】 brackets
        cited_indices = set(re.findall(r"(?:\[|【)(\d+)(?:\]|】)", response_text))
        
        references = []
        for index_str in cited_indices:
            try:
                # Convert "1" to index 0
                idx = int(index_str) - 1
                
                # Safety check: make sure ID exists in our docs list
                if 0 <= idx < len(docs):
                    doc = docs[idx]
                    
                    ref_data = {
                        'source_id': f"[{index_str}]",
                        'title': doc.metadata.get('title', 'Unknown Title'),
                        'chat_id': doc.metadata.get('chat_id', 'N/A'),
                        'turn_id': doc.metadata.get('turn_id', 'N/A'),
                        'timestamp': doc.metadata.get('timestamp', 'N/A'),
                        # Add snippet for debugging/display if needed
                        # 'snippet': doc.page_content[:100] + "..." 
                    }
                    references.append(ref_data)
            except ValueError:
                continue
        
        # Optional: Sort references numerically for cleaner output
        references.sort(key=lambda x: int(re.search(r'\d+', x['source_id']).group()))
        
        return references

    def query(self, query: str, chat_user: Optional[str] = "") -> Dict[str, Any]:
        """
        Main Public Method: Runs the full RAG pipeline.
        Returns a dictionary with 'answer' and 'references'.
        """
        # Step A: Retrieve
        docs = self._get_retrieved_docs(chat_user, query, k=10)
        
        # Step B: Format
        context_text = self._format_docs_with_ids(docs)
        
        # Step C: Generate
        ai_response = self.chain.invoke({
            "context": context_text,
            "question": query
        })
        
        # Step D: Extract References
        references = self._extract_references(ai_response, docs)
        
        return {
            "answer": ai_response,
            "references": references
        }

# --- Usage Example (Put this in your main.py or app.py) ---
if __name__ == "__main__":
    # 1. Initialize the Tool (Do this once when app starts)
    rag_tool = RAGCitationEngine()
    
    # 2. Use the Tool
    user_query = "Windows setting issues"
    print(f"Querying: {user_query}...")
    
    result = rag_tool.query(query=user_query, chat_user="")
    
    print("\n--- AI Response ---")
    print(result["answer"])
    
    print("\n--- References ---")
    for ref in result["references"]:
        print(ref)