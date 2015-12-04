"""
Resystem Scheduling Service.
Released under New BSD License.
Copyright © 2015, Vadim Markovtsev :: Angry Developers LLC
All rights reserved.
Redistribution and use in source and binary forms, with or without
modification, are permitted provided that the following conditions are met:
    * Redistributions of source code must retain the above copyright
      notice, this list of conditions and the following disclaimer.
    * Redistributions in binary form must reproduce the above copyright
      notice, this list of conditions and the following disclaimer in the
      documentation and/or other materials provided with the distribution.
    * Neither the name of the Angry Developers LLC nor the
      names of its contributors may be used to endorse or promote products
      derived from this software without specific prior written permission.
THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS" AND
ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE IMPLIED
WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE
DISCLAIMED. IN NO EVENT SHALL VADIM MARKOVTSEV BE LIABLE FOR ANY
DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES
(INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES;
LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND
ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT
(INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE OF THIS
SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.
"""


from setuptools import setup
import os


def parse_requirements():
    path = os.path.join(os.path.dirname(__file__), "requirements.txt")
    reqs = []
    with open(path, "r") as fin:
        for r in fin.read().split("\n"):
            r = r.strip()
            if r.startswith("#") or not r:
                continue
            if r.startswith("git+"):
                print("Warning: git dependencies cannot be used in setuptools "
                      "(%s)" % r)
                continue
            if not r.startswith("-r"):
                reqs.append(r)
    return reqs


setup(
    name="res-scheduling",
    description="Resystem Scheduling Service",
    version="1.0.2",
    license="New BSD",
    author="Vadim Markovtsev",
    author_email="gmarkhor@gmail.com",
    url="https://github.com/AngryDevelopersLLC/res-scheduler",
    download_url='https://github.com/AngryDevelopersLLC/res-scheduler',
    packages=["res.scheduling"],
    install_requires=parse_requirements(),
    package_data={"": [
        'res/scheduling/requirements/base.txt',
        'res/scheduling/res_scheduling.service',
        'res/scheduling/run.sh']},
    classifiers=[
        "Development Status :: 5 - Production/Stable",
        "Environment :: Console",
        "Intended Audience :: Developers",
        "License :: OSI Approved :: BSD License",
        "Operating System :: POSIX",
        "Programming Language :: Python :: 3.4",
        "Programming Language :: Python :: 3.5",
    ]
)
