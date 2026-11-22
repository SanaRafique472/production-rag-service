# MASTER PROMPT — Production-Grade RAG Chatbot

## YOUR ROLE

Act as a Senior AI Engineer, RAG Architect, Full-Stack Developer, Backend Engineer, Security Engineer, and QA Engineer.

I am developing an academic portfolio project titled:

**Production RAG Service — Document-Based Question Answering System**

I have provided the complete project assignment PDF. Read and understand the entire document before making any decisions.

Your responsibility is to help me build a professional, production-grade RAG application that satisfies ALL mandatory assignment requirements and implements as many valuable bonus features as realistically possible.

This is not a simple chatbot or a basic working demo. It must be a properly engineered, evaluated, tested, documented, and deployable application.

# CRITICAL DEVELOPMENT RULE

**DO NOT START CODING IMMEDIATELY.**

We will work in two major stages:

STAGE 1: COMPLETE PROJECT PLANNING.

STAGE 2: IMPLEMENTATION AFTER MY APPROVAL.

First, analyze the assignment, prepare the complete architecture and development plan, and present it to me.

Wait for my explicit approval before creating or modifying project files.

Do not skip planning or make major architectural decisions without explaining them.

---

# STAGE 1 — COMPLETE PROJECT PLANNING

## 1. Assignment Analysis

Carefully extract every requirement from the provided PDF.

Organize them into:

- Mandatory features.
- Optional bonus features.
- Testing requirements.
- Evaluation requirements.
- Security requirements.
- Documentation requirements.
- GitHub requirements.
- Deployment requirements.
- Demo video requirements.

Create a requirement traceability checklist mapping every assignment requirement to its planned implementation and verification method.

Do not overlook any requirement.

## 2. System Architecture

Design a complete production-oriented architecture.

The application should follow this workflow:

Document Upload → Validation → Text Extraction → Cleaning → Chunking → Embedding → Vector Database → Query Processing → Retrieval → Reranking → LLM Generation → Citation Validation → Final Answer.

Explain every component and how data flows between them.

Provide:

- System architecture diagram.
- RAG pipeline diagram.
- Backend architecture.
- Frontend architecture.
- Database architecture.
- API communication flow.
- Security architecture.
- Evaluation architecture.

Use Mermaid diagrams where appropriate.

## 3. Technology Selection

Research and compare suitable technologies for:

- Backend framework.
- Frontend framework.
- LLM provider and model.
- Embedding model.
- Vector database.
- RAG framework.
- Reranking model.
- Document extraction libraries.
- Authentication.
- Monitoring.
- Evaluation.
- Testing.
- Deployment.

For each technology, explain:

1. Why it is suitable.
2. Available alternatives.
3. Quality and accuracy.
4. Cost.
5. Latency.
6. Scalability.
7. Privacy considerations.
8. Limitations.

Recommend the best practical combination for this project.

Do not choose technologies just because they are popular.

Verify compatibility and current availability before recommending external services.

## 4. Database Design

Design the required database schema.

Consider:

- Users.
- Documents.
- Document metadata.
- Document hashes.
- Document chunks.
- Chunk metadata.
- Chat sessions.
- Chat messages.
- Evaluation records.

Explain relationships, indexing, ownership and deletion behavior.

## 5. API Design

Plan REST API endpoints, including:

Authentication:
- POST /auth/signup
- POST /auth/login
- GET /auth/me

Documents:
- POST /documents/upload
- GET /documents
- GET /documents/{document_id}
- DELETE /documents/{document_id}

Questions:
- POST /chat/ask
- GET /chat/history

Monitoring:
- GET /health
- GET /metrics

Evaluation endpoints only if genuinely needed.

Define request and response schemas, status codes, validation rules and error handling.

## 6. Frontend Design

Plan a modern, responsive React dashboard.

Include:

- Login and signup.
- Main dashboard.
- Document upload area.
- Uploaded documents list.
- Document deletion.
- Chat interface.
- Source citations.
- Retrieved chunks viewer.
- Similarity scores.
- Number of retrieved chunks.
- Loading indicators.
- Error states.
- Evaluation dashboard.
- Monitoring dashboard.

Create a professional UI/UX design specification with consistent colors, typography, spacing and components.

The frontend must clearly show the actual retrieved chunks for every answer.

## 7. RAG Retrieval Strategy

Plan and justify:

- Text extraction.
- Chunking strategy.
- Chunk size.
- Chunk overlap.
- Embedding generation.
- Vector similarity.
- Top-K retrieval.
- Similarity threshold.
- Metadata filtering.
- Hybrid search if appropriate.
- Reranking.
- Context construction.
- Citation generation.
- Refusal strategy.

Plan at least two retrieval configurations for an actual experimental comparison.

## 8. Security and Guardrails

Design protections against:

- Prompt injection.
- Jailbreak attempts.
- Malicious uploaded documents.
- Instructions hidden inside documents.
- Oversized files.
- Unsupported formats.
- Empty documents.
- Excessively long questions.
- Off-topic questions.
- Hallucinated answers.
- Fabricated citations.
- System prompt leakage.
- API key leakage.
- Unauthorized document access.

Use defense-in-depth instead of relying only on prompt instructions.

## 9. Evaluation Strategy

Plan a reproducible evaluation system.

Requirements:

- Minimum 25 golden questions.
- At least 5 unanswerable questions.
- Questions with paraphrasing.
- Multi-part questions.
- Correct document and page/chunk references.
- Reference answers.

Metrics:

- Hit Rate@K.
- Recall@K.
- MRR.
- Faithfulness.
- Answer relevancy.
- Answer correctness.
- Context precision.
- Context recall.
- Refusal accuracy.

Use RAGAS and/or a properly validated LLM-as-a-judge.

Compare at least two configurations using actual experiments.

Never invent evaluation results.

## 10. Testing Strategy

Testing must follow this order:

FIRST:
Jupyter notebooks for:
- PDF extraction.
- DOCX extraction.
- Text cleaning.
- Chunking.
- Embeddings.
- Vector storage.
- Retrieval.
- Reranking.
- Answer generation.
- Citation validation.

SECOND:
API testing using pytest and FastAPI TestClient.

Test successful operations and failure scenarios.

Include duplicate uploads, document deletion, invalid file types, empty documents, unanswerable questions, authentication failures, prompt injection and service failures.

All tests must actually be executed.

## 11. Complete Folder Structure

Propose a clean, modular and maintainable project structure.

Separate:

- Backend.
- Frontend.
- Ingestion.
- Retrieval.
- Generation.
- Guardrails.
- Database.
- Authentication.
- Evaluation.
- Notebooks.
- Tests.
- Documentation.
- Deployment.

Explain the responsibility of every important directory and file.

## 12. Documentation Planning

Plan these deliverables:

- README.md
- DESIGN.md
- EVAL.md
- requirements.txt
- .env.example
- .gitignore
- AI_USAGE.md
- Jupyter notebooks.
- API testing screenshots.
- Demo video script.

## 13. GitHub Workflow

Plan:

- Public GitHub repository.
- Main stable branch.
- Feature branches.
- Meaningful commit messages.
- Pull request workflow.
- Environment secret protection.

Never commit real API keys or credentials.

---

# STAGE 1 FINAL OUTPUT

Before coding, provide me:

1. Complete assignment analysis.
2. Mandatory vs bonus feature checklist.
3. Recommended technology stack.
4. Architecture diagrams.
5. Database schema.
6. API endpoint specification.
7. Frontend design plan.
8. RAG pipeline design.
9. Security strategy.
10. Evaluation strategy.
11. Testing strategy.
12. Complete folder structure.
13. Development phases in implementation order.
14. Estimated time for each phase.
15. Potential technical risks and solutions.
16. Final requirement traceability matrix.

Then STOP.

Ask me to approve the architecture and plan.

Do not generate implementation code until I approve.

---

# STAGE 2 — DEVELOPMENT AFTER MY APPROVAL

After I approve the plan, start development phase by phase.

## Development Rules

1. Implement actual working functionality, not placeholders.
2. Do not leave TODOs for mandatory requirements.
3. Write clean, modular, maintainable Python code.
4. Use type hints and meaningful docstrings.
5. Keep business logic outside FastAPI routes.
6. Use async I/O for external services where appropriate.
7. Implement timeouts, retries and proper exception handling.
8. Load secrets through environment variables.
9. Validate all uploaded files.
10. Implement ownership-based document access.
11. Ensure document deletion removes all associated vectors.
12. Ensure every citation points to an actually retrieved chunk.
13. Never fabricate evaluation numbers.
14. Execute tests instead of merely writing them.
15. Keep dependencies pinned.
16. Maintain documentation as features are implemented.

## Development Workflow

For each phase:

1. Explain what will be implemented.
2. Show which files will be created or modified.
3. Implement the functionality.
4. Explain important code and architectural decisions.
5. Run relevant tests.
6. Show actual test results.
7. Fix errors before moving forward.
8. Update the requirement checklist.
9. Wait for my confirmation before starting the next major phase.

Do not overwrite existing project files without checking their contents first.

Do not silently remove working functionality.

## Production Quality Requirements

The completed application must have:

- Functional FastAPI backend.
- Modern responsive React frontend.
- PDF and DOCX ingestion.
- Duplicate detection.
- Document management.
- Vector retrieval.
- Optional reranking based on measured benefit.
- Grounded AI answers.
- Verifiable source citations.
- Retrieved chunks with scores.
- Unanswerable question handling.
- Input and output guardrails.
- Prompt injection and jailbreak protection.
- JWT authentication.
- User-specific document isolation.
- Multilingual question support.
- Reproducible retrieval evaluation.
- RAGAS evaluation.
- Monitoring and logging.
- Jupyter notebooks.
- Automated unit and API tests.
- Docker configuration.
- Complete documentation.
- Deployment readiness.

## Final Acceptance Criteria

Do not declare the project complete until:

1. All mandatory requirements are implemented.
2. All implemented features have been tested.
3. Jupyter notebooks are included.
4. Evaluation has been executed on a real golden dataset.
5. At least two configurations have been compared.
6. Real evaluation metrics are documented.
7. Guardrail tests have passed.
8. Frontend and backend work together.
9. API documentation is accessible.
10. GitHub repository is organized.
11. Setup instructions work.
12. Demo video script covers all required demonstrations.

If a service requires an API key, explain how to configure it. Never pretend an external service was tested if credentials were unavailable.

# IMPORTANT: MY LEARNING REQUIREMENT

I am a student and must be able to explain this project to my instructor.

Do not simply generate large amounts of code.

Explain:

- What each important file does.
- Why a particular library is used.
- How RAG works internally.
- How embeddings and vector similarity work.
- How retrieval scores are calculated.
- How citations are generated.
- How evaluation metrics work.
- How security protections work.
- Why architectural decisions were made.

Use simple explanations alongside professional implementation.

I want a project that I can confidently demonstrate, defend technically, and maintain independently.

**START WITH STAGE 1 ONLY. READ THE COMPLETE PDF, ANALYZE THE REQUIREMENTS, AND PRESENT THE PROJECT PLAN. DO NOT CODE YET.**