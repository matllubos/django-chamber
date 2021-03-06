from setuptools import setup, find_packages

from chamber.version import get_version


setup(
    name="django-chamber",
    version=get_version(),
    description="Utilities library meant as a complement to django-is-core.",
    author="Lubos Matl, Oskar Hollmann",
    author_email="matllubos@gmail.com, oskar@hollmann.me",
    url="http://github.com/druids/django-chamber",
    packages=find_packages(),
    package_dir={"chamber": "chamber"},
    classifiers=[
        "Development Status :: 4 - Beta",
        "Environment :: Web Environment",
        "Intended Audience :: Developers",
        "License :: OSI Approved :: GNU Library or Lesser General Public License (LGPL)",
        "Operating System :: OS Independent",
        "Programming Language :: Python",
        "Programming Language :: Python :: 2.7",
        "Programming Language :: Python :: 3.5",
        "Framework :: Django",
    ],
    install_requires=[
        'Django>=1.8',
        'Unidecode>=0.04.17',
        'pyprind==2.9.9',
        'six>=1.10.0',
        'filemagic>=1.6',
    ],
)
