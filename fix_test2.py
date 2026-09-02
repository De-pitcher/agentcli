with open('C:\\Users\\sam\\Documents\\sparkz\\agentcli\\tests\\test_subagents.py', 'r', encoding='utf-8', errors='replace') as f:
    content = f.read()

old_test = 'test_search_not_implemented(self) -> None:\n\n        agent = WebSearchAgent()\n\n        task = SubAgentTask(agent_type=SubAgentType.WEB_SEARCH, payload={"query": "python asyncio"})\n\n        result = await agent.run(task)\n\n        assert result.success is False\n\n        assert "Web search is not yet implemented" in str(result.error)'

new_test = '''test_search_works(self) -> None:
        agent = WebSearchAgent()

        task = SubAgentTask(agent_type=SubAgentType.WEB_SEARCH, payload={
            "query": "python asyncio",
            "provider": "duckduckgo",
        })

        result = await agent.run(task)

        assert result.success is True
        assert result.output["count"] > 0
        assert len(result.output["results"]) > 0
        assert "provider" in result.output'''

if old_test in content:
    content = content.replace(old_test, new_test)
    with open('C:\\Users\\sam\\Documents\\sparkz\\agentcli\\tests\\test_subagents.py', 'w', encoding='utf-8') as f:
        f.write(content)
    print('Test updated successfully')
else:
    print('Old test not found exactly')
    # Find the test function
    idx = content.find('def test_search_not_implemented')
    if idx >= 0:
        # Find the end of the function
        next_def = content.find('def test_', idx + 1)
        if next_def == -1:
            next_def = len(content)
        print('Found at', idx)
        print(repr(content[idx:next_def]))