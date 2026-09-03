
def multiply(x, y):
    return x * y

def register_tools(registry):
    registry.register_callable("multiplier", multiply)
