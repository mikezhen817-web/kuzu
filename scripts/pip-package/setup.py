import shutil
import subprocess
import multiprocessing
import os
import sys

from setuptools import setup, Extension
from setuptools.command.build_ext import build_ext
from setuptools.command.build_py import build_py as _build_py

base_dir = os.path.dirname(__file__)

with open(os.path.join(base_dir, 'kuzu-source', 'tools', 'python_api', 'requirements_dev.txt'),encoding="utf-8") as f:
    requirements = f.read().splitlines()


def _get_kuzu_version():
    cmake_file = os.path.join(base_dir, 'kuzu-source', 'CMakeLists.txt')
    with open(cmake_file,encoding='utf-8') as f:
        for line in f:
            if line.startswith('project(Kuzu VERSION'):
                raw_version = line.split(' ')[2].strip()
                version_nums = raw_version.split('.')
                if len(version_nums) <= 3:
                    return raw_version
                else:
                    dev_suffix = version_nums[3]
                    version = '.'.join(version_nums[:3])
                    version += ".dev%s" % dev_suffix
                    return version

kuzu_version = _get_kuzu_version()
print("The version of this build is %s" % kuzu_version)


class CMakeExtension(Extension):
    def __init__(self, name: str, sourcedir: str = "") -> None:
        super().__init__(name, sources=[])
        self.sourcedir = os.path.abspath(sourcedir)


class CMakeBuild(build_ext):
    def build_extension(self, ext: CMakeExtension) -> None:
        self.announce("Building native extension...")
        env_vars = os.environ.copy()
        python_version = '.'.join(
            (str(sys.version_info.major), str(sys.version_info.minor)))
        self.announce("Python version is %s" % python_version)
        env_vars['PYBIND11_PYTHON_VERSION'] = python_version
        env_vars['PYTHON_EXECUTABLE'] = sys.executable

        # macOS 配置保持不变...
        if sys.platform == 'darwin':
            archflags = os.getenv("ARCHFLAGS", "")
            if len(archflags) > 0:
                self.announce("The ARCHFLAGS is set to '%s'." % archflags)
                if archflags == "-arch arm64":
                    env_vars['CMAKE_OSX_ARCHITECTURES'] = "arm64"
                elif archflags == "-arch x86_64":
                    env_vars['CMAKE_OSX_ARCHITECTURES'] = "x86_64"
                else:
                    self.announce("The ARCHFLAGS is not valid and will be ignored.")
            else:
                self.announce("The ARCHFLAGS is not set.")
            deploy_target = os.getenv("MACOSX_DEPLOYMENT_TARGET", "")
            if len(deploy_target) > 0:
                self.announce("The deployment target is set to '%s'." % deploy_target)
                env_vars['CMAKE_OSX_DEPLOYMENT_TARGET'] = deploy_target

        build_dir = os.path.join(ext.sourcedir, 'kuzu-source')
        
        try:
            num_cores = int(os.environ['NUM_THREADS'])
            self.announce("Using %d cores for building the native extension." % num_cores)
        except:
            self.announce("NUM_THREADS is not set. Using all available cores.")
            num_cores = multiprocessing.cpu_count()

        # ======================================================================
        # [PR FIX] Robust Windows Build Logic
        # ======================================================================
        # Problem: Default setup.py logic often fails on Windows sdist builds due to:
        # 1. CMake generator conflicts (e.g., finding Ninja but failing to use it).
        # 2. Missing .sln files if CMake wasn't configured with the correct generator.
        # 3. Difficulty locating the generated .pyd artifact in complex build trees.
        # Solution: Force clean cache, explicitly use VS generator, and robustly copy artifacts.
        # ======================================================================


        if sys.platform == 'win32':
            import glob
            
        # 1. [FIX] Clean CMake cache to prevent generator conflicts
        # This ensures we switch cleanly to Visual Studio generator even if
        # previous runs tried to use Ninja.


            cache_file = os.path.join(build_dir, 'CMakeCache.txt')
            if os.path.exists(cache_file):
                self.announce("Detected old CMake cache. Removing it to force VS generation...")
                os.remove(cache_file)
            
        # 2. [FIX] Locate or Generate Visual Studio Solution
        # Check for existing .sln files to avoid redundant reconfiguration.    
        #========================================================================

            sln_files = glob.glob(os.path.join(build_dir, "*.sln"))
            

            if not sln_files:
                self.announce("No .sln file found. Running CMake to generate Visual Studio project...")
                cmake_args = [
                    'cmake',
                    '.',
                    '-G', 'Visual Studio 18 2026',
                    '-A', 'x64',
                    '-DCMAKE_BUILD_TYPE=Release'
                ]

                result = subprocess.run(cmake_args, cwd=build_dir, check=True, env=env_vars)
                

                sln_files = glob.glob(os.path.join(build_dir, "*.sln"))
                

                vcxproj_files = glob.glob(os.path.join(build_dir, "ALL_BUILD.vcxproj"))
                
                if not sln_files and not vcxproj_files:
                    raise FileNotFoundError(
                        f"CMake failed to generate .sln or ALL_BUILD.vcxproj in {build_dir}. "
                        "Check CMake output above for errors."
                    )
                
                if sln_files:
                    solution_file = sln_files[0]
                    self.announce(f"Found solution file: {os.path.basename(solution_file)}")
                else:

                    solution_file = vcxproj_files[0]
                    self.announce(f"No .sln found, using project file: {os.path.basename(solution_file)}")
            else:
                solution_file = sln_files[0]
                self.announce(f"Using existing solution file: {os.path.basename(solution_file)}")
            

            self.announce("Building with MSBuild...")
            msbuild_exe = r"C:\Program Files (x86)\Microsoft Visual Studio\18\BuildTools\MSBuild\Current\Bin\MSBuild.exe"
            msbuild_args = [
                msbuild_exe,
                solution_file,
                '/p:Configuration=Release',
                '/p:Platform=x64',
                f'/m:{num_cores}'

            ]
            subprocess.run(msbuild_args, cwd=build_dir, check=True, env=env_vars)
            

            self.announce("Locating generated .pyd file...")
            pyd_path = None
            

            pyd_candidates = [
                os.path.join(build_dir, 'tools', 'python_api', 'Release', 'kuzu_core.pyd'),
                os.path.join(build_dir, 'tools', 'python_api', 'RelWithDebInfo', 'kuzu_core.pyd'),
                os.path.join(build_dir, 'Release', 'kuzu_core.pyd'),
                os.path.join(build_dir, 'x64', 'Release', 'kuzu_core.pyd'),
            ]
            
            for candidate in pyd_candidates:
                if os.path.exists(candidate):
                    pyd_path = candidate
                    break
            

            if not pyd_path:
                self.announce("Standard paths failed, scanning entire build directory...")
                for root, _, files in os.walk(build_dir):
                    for f in files:
                        if f.endswith('.pyd') and 'kuzu' in f.lower():
                            pyd_path = os.path.join(root, f)
                            self.announce(f"Found .pyd at: {pyd_path}")
                            break
                    if pyd_path:
                        break
            
            if not pyd_path:
                raise FileNotFoundError("Could not find generated .pyd file. Build may have failed or output path changed.")
            

            dst_dir = os.path.join(ext.sourcedir, 'kuzu')
            os.makedirs(dst_dir, exist_ok=True)
            shutil.copy(pyd_path, dst_dir)
            self.announce(f"✅ Native extension successfully copied to {dst_dir}")
            
        else:

            self.announce("Cleaning previous build...")
            subprocess.run(['make', 'clean'], cwd=build_dir, check=True, env=env_vars)
            self.announce("Building with make...")
            full_cmd = ['make', 'python', f'NUM_THREADS={num_cores}']
            subprocess.run(full_cmd, cwd=build_dir, check=True, env=env_vars)
            dst = os.path.join(ext.sourcedir, ext.name)
            shutil.rmtree(dst, ignore_errors=True)
            shutil.copytree(
                os.path.join(build_dir, 'tools', 'python_api', 'build', ext.name),
                dst
            )
            self.announce("Done copying native extension.")


class BuildExtFirst(_build_py):
    # Override the build_py command to build the extension first.
    def run(self):
        self.run_command("build_ext")
        return super().run()


setup(name='kuzu',
      version=kuzu_version,
      install_requires=[],
      ext_modules=[CMakeExtension(
          name="kuzu", sourcedir=base_dir)],
      description='An in-process property graph database management system built for query speed and scalability.',
      license='MIT',
      long_description=open(os.path.join(base_dir, "README.md"), 'r').read(),
      long_description_content_type="text/markdown",
      packages=["kuzu"],
      zip_safe=True,
      include_package_data=True,
      cmdclass={
          'build_py': BuildExtFirst,
          'build_ext': CMakeBuild,
      }
      )
