# Prompts used (in order)

All prompts were written in one claude.ai chat. Prompts 1 and 2 were for other written
assignments in the same course; prompts 3 onward are this project.

| # | Prompt (verbatim or summarised) | Result |
| --- | --- | --- |
| 1 | *(other assignment)* "Revise my assignment *Evaluating RAG and Agentic AI Systems: Techniques & Frameworks Ranked* … fact-check it, add missing techniques and frameworks, keep it to 3 pages." (with the .docx attached) | Revised .docx |
| 2 | *(other assignment)* "Write a detailed plan for the best way to build a RAG chatbot where users can upload their own documents … This is ONLY a written assignment. Do not build the chatbot." (with the Portfolio-01 PDF as a guide) | Design-plan document |
| 3 | **Master prompt**: full text in [`master_prompt.md`](master_prompt.md), with `Portfolio-01-Production-RAG-Service.pdf` attached. Stage 1 = plan only, wait for approval. | Project plan document |
| 4 | "develop kro ab" *(start development now)* | Implementation started (setup, ingestion) |
| 5 | "mjhy complete project develop kr k zip file do" *(develop the complete project and give me a zip file)* | Full project + zip |
| 6 | "how to run this project" | Run instructions (now in README.md) |
| 7 | "kia project jo portfolio 1 ki doc file baji hai os k according ha?" *(does the project match the Portfolio 1 document?)* | Requirement-by-requirement check against the PDF, list of gaps |
| 8 | "han dy do" *(yes, fix them)* | DESIGN/EVAL/AI_USAGE additions, request tracing, Swagger screenshots, new zip |
| 9 | "I am a complete beginner … Please inspect my entire project carefully … Do not make changes until I approve them." | Fresh-install inspection; found missing `greenlet` and the no-Docker data-loss issue |
| 10 | Questions while running it on my computer (Windows, Python 3.11): `.env` contents, "site cannot be reached", screenshots of errors | Step-by-step help; Python 3.11 compatibility changes (numpy, `TypeVar`, pyproject) |
| 11 | "aik word ki file dy do" *(give me a Word file)* | `docs/DocAnswer_Chalane_ka_Tareeqa.docx` run guide |
| 12 | "jo project bna k diya ha kiay es doc k according h?" *(does the project match the brief?)* | Re-check against the PDF; remaining items are mine (evaluation run, GitHub, video) |
| 13 | "mjhy test kr k compete project share kro" *(test and share the complete project)* | Fixes merged, clean-install tests on Python 3.11 and 3.13, final zip |
| 14 | "free api key mil sakti?" / "kahn se lu" / "docker k elwa kia use kr sakty?" | Where to get free keys; running without Docker |
| 15 | "es mei api k zero cradits h kuch free wla suggest kro" | Suggested Cohere Command A+ (free) instead of Claude |
| 16 | "cohere ki key se chal jye ga project?" / "free suggest ko api key kahn se mil sakti ji se project fully functional ho" | Explained the code change needed for a Cohere-only setup |
| 17 | My Cohere trial key + "doc english mei ho osi ko evalaute krye" *(write the docs in English and run the evaluation)* | Cohere Command A+ and judge added, live evaluation of A and B, EVAL.md results and failure analysis |
| 18 | "local py project ko run krna ha live ni krana" *(run locally, do not deploy)* | Deployment (Bonus H) skipped |
