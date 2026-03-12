"""Edit tool — reliable string replacement in files."""

from pathlib import Path

name = "edit"
description = (
    "Replace an exact string in a file with a new string. "
    "Provide old_string (must match exactly, including whitespace/indentation) "
    "and new_string. For creating new files, use old_string=\"\" with the full content as new_string."
)
input_schema = {
    "type": "object",
    "properties": {
        "file_path": {
            "type": "string",
            "description": "Absolute or relative path to the file.",
        },
        "old_string": {
            "type": "string",
            "description": "The exact string to find and replace. Use empty string to create a new file.",
        },
        "new_string": {
            "type": "string",
            "description": "The replacement string.",
        },
    },
    "required": ["file_path", "old_string", "new_string"],
}


def run(inp):
    path = Path(inp["file_path"])
    old = inp["old_string"]
    new = inp["new_string"]

    # create new file
    if old == "":
        if path.exists():
            return f"Error: file already exists: {path}. Provide old_string to edit it."
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(new)
        return f"Created {path} ({len(new.splitlines())} lines)"

    # edit existing file
    if not path.exists():
        return f"Error: file not found: {path}"

    content = path.read_text()
    count = content.count(old)

    if count == 0:
        return "Error: old_string not found in file."
    if count > 1:
        return f"Error: old_string matches {count} locations. Provide more context to make it unique."

    content = content.replace(old, new, 1)
    path.write_text(content)

    lines_removed = old.count("\n") + 1
    lines_added = new.count("\n") + 1
    return f"Edited {path} (-{lines_removed} +{lines_added} lines)"
