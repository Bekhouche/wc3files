"""No help text or message sends users to TODO.md (#169). TODO.md is in
neither the sdist nor the wheel, so an installed copy cannot follow such a
pointer. Docstrings and comments are for developers and may still cite it."""
import argparse
import ast
import os
import unittest

import _helpers  # noqa: F401
from wc3files.cli.main import build_parser

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, 'src', 'wc3files')


def _docstring_nodes(tree):
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            body = getattr(node, 'body', [])
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
                yield body[0].value


class TestNoTodoPointers(unittest.TestCase):

    def test_no_help_text_names_todo_md(self):
        parser = build_parser()
        texts = [parser.format_help()]
        for action in parser._actions:
            if isinstance(action, argparse._SubParsersAction):
                for name, sub in action.choices.items():
                    texts.append(sub.format_help())
        self.assertEqual([t[:80] for t in texts if 'TODO.md' in t], [])

    def test_no_message_string_names_todo_md(self):
        found = []
        for folder, _dirs, files in os.walk(SRC):
            for name in files:
                if not name.endswith('.py'):
                    continue
                path = os.path.join(folder, name)
                with open(path, encoding='utf-8') as handle:
                    tree = ast.parse(handle.read())
                docs = {id(node) for node in _docstring_nodes(tree)}
                for node in ast.walk(tree):
                    if (isinstance(node, ast.Constant) and isinstance(node.value, str)
                            and 'TODO.md' in node.value and id(node) not in docs):
                        found.append(f'{os.path.relpath(path, ROOT)}:{node.lineno}')
        self.assertEqual(found, [])


if __name__ == '__main__':
    unittest.main()
