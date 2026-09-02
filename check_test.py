with open('C:\\Users\\sam\\Documents\\sparkz\\agentcli\\tests\\test_agent_loop.py', 'r', encoding='utf-8', errors='replace') as f:
    content = f.read()

# Find the test
idx = content.find('test_real_planner_produces_plan_and_loop_runs')
if idx >= 0:
    next_def = content.find('def test_', idx + 1)
    if next_def == -1:
        next_def = len(content)
    test_content = content[idx:next_def]
    print(test_content)
    
    # Check what goal_criterion the test expects
    if 'goal_criterion' in test_content:
        print('\n--- goal_criterion references ---')
        import re
        for m in re.finditer(r'goal_criterion[^\n]*', test_content):
            print(m.group())