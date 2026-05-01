from core.llm.decision import LLMDecider
llm = LLMDecider(backend='openai', api_base='https://api.deepseek.com', api_key='sk-52148a07abea47ba81d32b3e7fed41f0', cloud_model='deepseek-chat')

queries = ['关闭音响', '打开热水器', '关闭热水器']
for q in queries:
    result = llm.plan_intent(q, q)
    print(f'Query: {q}')
    print(f'  intent_type: {result.get("intent_type")}')
    print(f'  route: {result.get("route")}')
    msg = result.get("reply_message") or ""
    print(f'  reply_message: {msg[:80]}')
    print()
