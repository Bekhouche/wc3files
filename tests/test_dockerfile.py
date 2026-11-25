"""The Dockerfile of wc3files-mcp and its .dockerignore (#198).

Docker is not needed here: CI builds the image. These catch a COPY of a file
the .dockerignore leaves out, which fails only at build time, and an
allow-list that lets the whole working folder into the build.
"""
import os
import re
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DOCKERFILE = os.path.join(ROOT, 'Dockerfile')


@unittest.skipUnless(os.path.exists(DOCKERFILE), 'the Dockerfile is only in the repository')
class TestDockerfile(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        with open(DOCKERFILE, encoding='utf-8') as f:
            cls.dockerfile = f.read()
        with open(os.path.join(ROOT, '.dockerignore'), encoding='utf-8') as f:
            lines = [line.strip() for line in f]
        cls.ignore = [line for line in lines if line and not line.startswith('#')]

    def test_ignore_is_an_allow_list(self):
        self.assertEqual(self.ignore[0], '*')
        allowed = {line[1:].rstrip('/') for line in self.ignore if line.startswith('!')}
        self.assertNotIn('private', allowed)
        self.assertNotIn('.', allowed)

    def test_copied_files_exist_and_are_allowed(self):
        allowed = {line[1:].rstrip('/') for line in self.ignore if line.startswith('!')}
        sources = []
        for line in self.dockerfile.splitlines():
            if line.startswith('COPY '):
                sources.extend(line.split()[1:-1])
        self.assertIn('setup.py', sources)
        self.assertIn('src', sources)
        for source in sources:
            self.assertTrue(os.path.exists(os.path.join(ROOT, source)), source)
            self.assertIn(source, allowed)

    def test_starts_the_mcp_server_on_data(self):
        with open(os.path.join(ROOT, 'setup.py'), encoding='utf-8') as f:
            self.assertIn("'wc3files-mcp=", f.read())
        self.assertIn('ENTRYPOINT ["wc3files-mcp"]', self.dockerfile)
        self.assertIn('CMD ["--root", "/data"]', self.dockerfile)
        self.assertRegex(self.dockerfile, re.compile(r'^USER (?!root)\w+', re.M))


if __name__ == '__main__':
    unittest.main()
