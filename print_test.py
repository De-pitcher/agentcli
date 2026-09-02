with open('C:\\Users\\sam\\Documents\\sparkz\\agentcli\\tests\\test_agent_loop.py', 'r', encoding='utf-8', errors='replace') as f:
    content = f.read()

idx = content.find('test_real_planner_produces_plan_and_loop_runs')
if idx >= 0:
    next_def = content.find('def test_', idx + 1)
    if next_def == -1:
        next_def = len(content)
    test_content = content[idx:next_def]
    with open('C:\\Users\\sam\\Documents\\sparkz\\agentcli\\test_output.txt', 'w', encoding='utf-8') as f:
        f.write(test_content)
    print("Written to file")