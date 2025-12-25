### This file contains code that takes s3 uri of single chat json and then load and ingest data into pinecone

import json
import boto3
from langchain_core.documents import Document
from langchain_text_splitters import RecursiveCharacterTextSplitter
import os
from pinecone import Pinecone, ServerlessSpec
from langchain_pinecone import PineconeVectorStore
from langchain_google_genai import GoogleGenerativeAIEmbeddings
from dotenv import load_dotenv
from urllib.parse import urlparse

load_dotenv()

lambda_client = boto3.client('lambda')

def invoke_lambda(test_event):
    try:
  
        response = lambda_client.invoke(
            FunctionName='ConversationAccess-Json-UAT',  
            InvocationType='RequestResponse',  
            Payload=json.dumps(test_event)  
        )
        
        # Read and parse the response
        response_payload = json.loads(response['Payload'].read().decode('utf-8'))
        
        # Return the Lambda response body
        return response_payload
    
    except Exception as e:
        print(f"Error invoking Lambda: {e}")
        return None

def s3_json_load_ingest(s3_uri: str, lambda_event: dict):
    # Call Lambda function with the event and get the response
    lambda_response = invoke_lambda(lambda_event)
    if not lambda_response:
        print("Error receiving response from Lambda.")
        return []

    # Extract the necessary fields from the Lambda response
    category_one = lambda_response["body"]["category_one"]
    category_two = lambda_response["body"]["category_two"]

    # Debugging: Log the probabilities of category_one and category_two
    print(f"Category One Probability: {category_one['probability']}, Name: {category_one['name']}")
    print(f"Category Two Probability: {category_two['probability']}, Name: {category_two['name']}")

    # Determine accessibility based on the probability of the categories
    
    if category_two["probability"]>=0.9:
        accessibility="work"
        accessibility_confidence_score=category_two["probability"]
    
    else:
        accessibility="personal"
        accessibility_confidence_score=1-category_two["probability"]
    

    # Log the final decision for debugging
    print(f"Final Decision - Accessibility: {accessibility}, Accessibility Confidence Score: {accessibility_confidence_score}")

    # Parse the S3 URI
    parsed_uri = urlparse(s3_uri)
    bucket_name = parsed_uri.netloc
    file_key = parsed_uri.path.lstrip('/')

    # Initialize S3 client
    s3_client = boto3.client('s3')

    try:
        # Download the file from S3
        response = s3_client.get_object(Bucket=bucket_name, Key=file_key)
        file_content = response['Body'].read().decode('utf-8')
        data = json.loads(file_content)

        print("Loaded data from S3.")

        # Check for different possible JSON structures
        chats = []

        if isinstance(data, dict) and "messages" in data:
            print("Detected single chat file structure.")
            chats = [data]  # Wrap it in a list for further processing
            
        elif isinstance(data, dict) and "data" in data:
            print("Detected wrapper structure with 'data' key.")
            chats = data["data"]
            
        elif isinstance(data, list):
            print("Detected list of chats.")
            chats = data
            
        else:
            print("JSON structure not recognized. Could not find 'messages' or 'data'.")
            return []

        docs = []

        # Iterate through the chats and create documents
        for chat in chats:
            if "messages" not in chat:
                print(f"No messages found for chat_id: {chat.get('chat_id')}")
                continue

            for msg in chat["messages"]:
                content = msg.get("message") or ""

                # Use extracted values in the metadata
                meta = {
                    "chat_engine": chat.get("chat_engine", os.getenv("CHAT_ENGINE")),
                    "chat_account": chat.get("chat_user", os.getenv('CHAT_USER')),
                    "chat_id": chat.get("chat_id"),
                    "title": chat.get("title"),
                    "chat_creation_time": chat.get("chat_creation_time"),
                    "turn_id": msg.get("turn_id"),
                    "author": msg.get("author"),
                    "turn_timestamp": msg.get("turn_timestamp"),
                    "accessibility": accessibility,
                    "accessibility_confidence_score": accessibility_confidence_score
                }

                docs.append(Document(page_content=content, metadata=meta))

        if docs:
            print(f"Created {len(docs)} documents.")
        else:
            print("No documents were created.")
            return []

        # Initialize text splitter - Increased chunk size for better RAG context
        splitter = RecursiveCharacterTextSplitter(
            chunk_size=150,  # Adjusted for better chunking
            chunk_overlap=30,
            separators=["\n\n", "\n", " ", ""],
        )

        chunked_docs = []

        # Split documents into chunks
        for d in docs:
            chunks = splitter.split_text(d.page_content or "")
            for i, chunk in enumerate(chunks):
                md = dict(d.metadata)
                md["chunk_index"] = i
                md["chunk_id"] = f"{md.get('chat_id')}::{md.get('turn_id')}::{i}"
                chunked_docs.append(Document(page_content=chunk, metadata=md))

        # Pinecone integration
        INDEX_NAME = os.getenv('PINECONE_INDEX', '')
        DIMENSION = 768

        pc = Pinecone(api_key=os.getenv('PINECONE_API_KEY', ''))

        # Check if the index exists or create it
        if INDEX_NAME not in [i["name"] for i in pc.list_indexes()]:
            pc.create_index(
                name=INDEX_NAME,
                dimension=DIMENSION,
                metric="cosine",
                spec=ServerlessSpec(cloud="aws", region="us-east-1"),
            )

        embeddings = GoogleGenerativeAIEmbeddings(model="models/text-embedding-004")

        # Create a PineconeVectorStore from the chunked documents
        vectorstore = PineconeVectorStore.from_documents(
            documents=chunked_docs,
            embedding=embeddings,
            index_name=INDEX_NAME,
        )

        print("Data ingested successfully.")

    except Exception as e:
        print(f"Error processing S3 file: {e}")
        return []



# Example test event to pass to Lambda
test_event = {
    "category_one": "personal",
    "category_two": "work",
    "file_path": "s3://il-raw-chats/agent_conversation/716b6580-0041-70b9-fb98-28d932d991be_19aa9238-3d46-46da-8bfd-a5b46486a6e4/pdf_to_json.json"
}

# Test the ingestion process
s3_json_load_ingest(s3_uri=test_event["file_path"], lambda_event=test_event)
