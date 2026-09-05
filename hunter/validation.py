"""Small JSON boundary validators shared by HTTP and stdio tools."""


def validate(value, schema, path="request"):
    """Validate the JSON Schema subset used by Hunter's request contracts."""
    expected = schema.get("type")
    types = {
        "object": lambda item: isinstance(item, dict),
        "array": lambda item: isinstance(item, list),
        "string": lambda item: isinstance(item, str),
        "integer": lambda item: isinstance(item, int) and not isinstance(item, bool),
        "number": lambda item: isinstance(item, (int, float)) and not isinstance(item, bool),
        "boolean": lambda item: isinstance(item, bool),
        "null": lambda item: item is None,
    }
    accepted = expected if isinstance(expected, list) else [expected] if expected else []
    if accepted and not any(types[kind](value) for kind in accepted):
        raise ValueError(f"{path} must be {' or '.join(accepted)}.")
    if "enum" in schema and value not in schema["enum"]:
        raise ValueError(f"{path} must be one of the supported values.")
    if isinstance(value, dict):
        for key in schema.get("required", []):
            if key not in value:
                raise ValueError(f"{path}.{key} is required.")
        properties = schema.get("properties", {})
        for key, item in value.items():
            if key in properties:
                validate(item, properties[key], f"{path}.{key}")
    if isinstance(value, list) and "items" in schema:
        for index, item in enumerate(value):
            validate(item, schema["items"], f"{path}[{index}]")
    return value
