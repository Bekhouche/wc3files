import os
import re
from setuptools import setup, find_packages

current = os.path.abspath(os.path.dirname(__file__))
with open(os.path.join(current, 'README.md'), encoding='utf-8') as f:
    long_description = f.read()

# Read the version rather than repeating it. It was declared in both places
# before and they had drifted - setup.py said 0.0.1 while the package itself
# reported 0.02 - so `pip show` and `wc3files.__version__` disagreed.
with open(os.path.join(current, 'src', 'wc3files', 'version.py'),
          encoding='utf-8') as f:
    version = re.search(r"__version__\s*=\s*'([^']+)'", f.read()).group(1)

setup(
    name='wc3files',
    version=version,
    package_dir={'': 'src'},
    packages=find_packages(where='src'),
    package_data={'wc3files.data': ['reference/*.txt'],
                   'wc3files.wtg': ['gamedata/*.txt'],
                   'wc3files.w3o': ['gamedata/*.slk', 'gamedata/*.txt', 'gamedata/*.json',
                                    'gamedata/strings/*.txt'],
                   # common.ai as well as the .j scripts: *.j alone left it
                   # out of the wheel.
                   'wc3files.scripts': ['gamedata/*.j', 'gamedata/*.ai'],
                   # The bundled icon-border art, which borders.py loads at
                   # runtime. It had no entry at all, so border_path() could
                   # not find anything in an installed copy.
                   'wc3files.img': ['gamedata/borders/*.png']},
    include_package_data=True,
    install_requires=[
        # Imported at module import time, so the package cannot even be
        # imported without them. This list was empty, which made an
        # installed copy fail on `import wc3files` with ModuleNotFoundError.
        'loguru',
        'tqdm',
        # Used for a single pd.isna() call in table/slk_file.py.
        'pandas',
    ],
    entry_points={
        'console_scripts': ['wc3files=wc3files.cli.main:main'],
    },
    author='Salah Eddine Bekhouche',
    author_email='bekhouchesalah@gmail.com',
    description='Warcraft III maps files [reader/writer]',
    long_description=long_description,
    long_description_content_type='text/markdown',
    url='https://github.com/Bekhouche/wc3files',
    # The README has always claimed MIT and pointed at a LICENSE file that
    # did not exist; both the file and this declaration close that gap.
    license='MIT',
    license_files=['LICENSE'],
    # The package has run under 3.9 through 3.14 and uses no 3.10-only
    # syntax, but it does use a 3.9 dict merge, so 3.9 is the floor.
    python_requires='>=3.9',
    classifiers=[
        'Development Status :: 4 - Beta',
        'Intended Audience :: Developers',
        'License :: OSI Approved :: MIT License',
        'Operating System :: OS Independent',
        'Programming Language :: Python :: 3',
        'Programming Language :: Python :: 3.9',
        'Programming Language :: Python :: 3.10',
        'Programming Language :: Python :: 3.11',
        'Programming Language :: Python :: 3.12',
        'Programming Language :: Python :: 3.13',
        'Topic :: Games/Entertainment',
        'Topic :: Software Development :: Libraries :: Python Modules',
    ],
)