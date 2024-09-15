#! /usr/bin/env python3
#------------------------------------------------------------------------------

import os
import sys
import subprocess
import re
import argparse
import tempfile
import datetime
import difflib
import platform
#------------------------------------------------------------------------------

mypath = '.'

def runCmd(cmd, data=None):
    if input is None:
        p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        stdout, stderr = p.communicate()
    else:
        p = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        stdout, stderr = p.communicate(input=data)

    return stdout.decode('utf-8'), stderr.decode('utf-8'), p.returncode
#------------------------------------------------------------------------------

def cleanStderr(stderr, fileName=None):
    if fileName is not None:
        stderr = stderr.replace(fileName, '.tmp.cpp')
    else:
        stderr = re.sub('(.*).cpp:', '.tmp:', stderr)
#    stderr = re.sub('[\n ](.*).cpp:', '\\1%s:' %(fileName), stderr)

    # Replace paths, as for example, the STL path differs from a local build to Travis-CI at least for macOS
    stderr = re.sub('/(.*)/(.*?:[0-9]+):', '... \\2:', stderr)
    stderr = re.sub('RecoveryExpr 0x[a-f0-9]+ ', 'RecoveryExpr ', stderr)

    return stderr
#------------------------------------------------------------------------------

def testCompare(tmpFileName, stdout, expectFile, f, args, time):
    expect = open(expectFile, 'r', encoding='utf-8').read()

    # align line-endings under Windows to Unix-style
    if os.name == 'nt':
        stdout = stdout.replace('\r\n', '\n')

    if stdout != expect:
        print('[FAILED] %s - %s' %(f, time))

        for line in difflib.unified_diff(expect.splitlines(keepends=True), stdout.splitlines(keepends=True), fromfile=expectFile, tofile='stdout', n=3):
            print('%s' %((line[1:] if line.startswith(' ') else line) ), end='')
    else:
        print('[PASSED] %-50s - %s' %(f, time))
        return True

    return False
#------------------------------------------------------------------------------

def testCompile(tmpFileName, f, args, fileName, cppStd):
    if os.name == 'nt':
        cppStd = cppStd.replace('-std=', '/std:')
        cppStd = cppStd.replace('2a', 'latest')

    cmd = [args['cxx'], cppStd, '-D__cxa_guard_acquire(x)=true', '-D__cxa_guard_release(x)', '-D__cxa_guard_abort(x)', '-I', os.getcwd()]

    if os.name != 'nt':
        arch = platform.architecture()[0]
        if (arch != '64bit') or ((arch == '64bit') and (sys.platform == 'darwin')):
            cmd.append('-m64')
    else:
        cmd.extend(['/nologo', '/EHsc', '/IGNORE:C4335']) # C4335: mac file format detected. EHsc assume only C++ functions throw exceptions.

    # GCC seems to dislike empty ''
    if '-std=c++98' == cppStd:
        cmd += ['-Dalignas(x)=']

    cmd += ['-c', tmpFileName]

    stdout, stderr, returncode = runCmd(cmd)

    compileErrorFile = os.path.join(mypath, fileName + '.cerr')
    if 0 != returncode:
        if os.path.isfile(compileErrorFile):
            ce = open(compileErrorFile, 'r', encoding='utf-8').read()
            stderr = cleanStderr(stderr, tmpFileName)

            if ce == stderr:
                print(f'[PASSED] Compile: {f}')
                return True, None

        compileErrorFile = os.path.join(mypath, fileName + '.ccerr')
        if os.path.isfile(compileErrorFile):
                ce = open(compileErrorFile, 'r', encoding='utf-8').read()
                stderr = stderr.replace(tmpFileName, '.tmp.cpp')

                if ce == stderr:
                    print('f[PASSED] Compile: {f}')
                    return True, None

        print(f'[ERROR] Compile failed: {f}')
        print(stderr)
    else:
        if os.path.isfile(compileErrorFile):
            print('unused file: %s' %(compileErrorFile))

        ext = 'obj' if os.name == 'nt' else 'o'

        objFileName = '%s.%s' %(os.path.splitext(os.path.basename(tmpFileName))[0], ext)
        os.remove(objFileName)

        print(f'[PASSED] Compile: {f}')
        return True, None

    return False, stderr
#------------------------------------------------------------------------------


def main():
    parser = argparse.ArgumentParser(description='Description of your program')
    parser.add_argument('--insights',       help='C++ Insights binary',  required=True)
    parser.add_argument('--cxx',            help='C++ compiler to used', default='/usr/local/clang-current/bin/clang++')
    parser.add_argument('--failure-is-ok',  help='Failing tests are ok', default=False, action='store_true')
    parser.add_argument('--update-tests',   help='Update failing tests', default=False, action='store_true')
    parser.add_argument('--std',            help='C++ Standard to used', default='c++17')
    parser.add_argument('--use-libcpp',     help='Use libst++',          default=False, action='store_true')
    parser.add_argument('--llvm-prof-dir',  help='LLVM profiles data dir', default='')
    parser.add_argument('args', nargs=argparse.REMAINDER)
    args = vars(parser.parse_args())

    insightsPath  = args['insights']
    remainingArgs = args['args']
    bFailureIsOk  = args['failure_is_ok']
    bUpdateTests  = args['update_tests']
    defaultCppStd = f"-std={args['std']}"

    if args['llvm_prof_dir'] != '':
        os.environ['LLVM_PROFILE_FILE'] = os.path.join(args['llvm_prof_dir'], 'prof%p.profraw')

    if 0 == len(remainingArgs):
        cppFiles = [f for f in os.listdir(mypath) if (os.path.isfile(os.path.join(mypath, f)) and f.endswith('.cpp'))]
    else:
        cppFiles = remainingArgs

    filesPassed     = 0
    missingExpected = 0
    crashes         = 0
    ret             = 0

    regEx         = re.compile('.*cmdline:(.*)')
    regExInsights = re.compile('.*cmdlineinsights:(.*)')

    for f in sorted(cppFiles):
        fileName     = os.path.splitext(f)[0]
        expectFile   = os.path.join(mypath, fileName + '.expect')
        ignoreFile   = os.path.join(mypath, fileName + '.ignore')
        cppStd       = defaultCppStd
        insightsOpts = ''

        fh = open(f, 'r', encoding='utf-8')
        fileHeader = fh.readline()
        fileHeader += fh.readline()
        m = regEx.search(fileHeader)
        if m is not None:
            cppStd = m.group(1)

        m = regExInsights.search(fileHeader)
        if m is not None:
            insightsOpts = m.group(1).split(' ')

        if not os.path.isfile(expectFile) and not os.path.isfile(ignoreFile):
            print(f'Missing expect/ignore for: {f}')
            missingExpected += 1
            continue

        if os.path.isfile(ignoreFile):
            print(f'Ignoring: {f}')
            filesPassed += 1
            continue

        cmd = [insightsPath, f]

        if args['use_libcpp']:
            cmd.append('-use-libc++')


        if len(insightsOpts):
            cmd.extend(insightsOpts)

        cmd.extend(['--', cppStd, '-m64'])

        begin = datetime.datetime.now()
        stdout, stderr, returncode = runCmd(cmd)
        end   = datetime.datetime.now()

        if 0 != returncode:
            compileErrorFile = os.path.join(mypath, fileName + '.cerr')
            if os.path.isfile(compileErrorFile):
                ce = open(compileErrorFile, 'r', encoding='utf-8').read()

                # Linker errors name the tmp file and not the .tmp.cpp, replace the name here to be able to suppress
                # these errors.
                ce = re.sub('(.*).cpp:', '.tmp:', ce)
                ce = re.sub('(.*).cpp.', '.tmp:', ce)
                stderr = re.sub('(Error while processing.*.cpp.)', '', stderr)
                stderr = cleanStderr(stderr)

                # The cerr output matches and the return code says that we hit a compile error, accept it as passed
                if ((ce == stderr) and (1 == returncode)) or os.path.exists(os.path.join(mypath, fileName + '.failure')):
                    print(f'[PASSED] Transform: {f}')
                    filesPassed += 1
                    continue
                else:
                    print(f'[ERROR] Transform: {f}')
                    ret = 1

            crashes += 1
            print(f'Insight crashed for: {f} with: {returncode}')
            print(stderr)

            if not bUpdateTests:
                continue

        fd, tmpFileName = tempfile.mkstemp('.cpp')
        try:
            with os.fdopen(fd, 'w', encoding='utf-8') as tmp:
                # write the data to the temp file
                tmp.write(stdout)

            equal = testCompare(tmpFileName, stdout, expectFile, f, args, end-begin)
            bCompiles, stderr = testCompile(tmpFileName, f, args, fileName, cppStd)
            compileErrorFile = os.path.join(mypath, fileName + '.cerr')


            if (bCompiles and equal) or bFailureIsOk:
                filesPassed += 1
            elif bUpdateTests:
                if bCompiles and not equal:
                    open(expectFile, 'w', encoding='utf-8').write(stdout)
                    print('Updating test')
                elif not bCompiles and os.path.exists(compileErrorFile):
                    open(expectFile, 'w', encoding='utf-8').write(stdout)
                    open(compileErrorFile, 'w', encoding='utf-8').write(stderr)
                    print('Updating test cerr')


        finally:
            os.remove(tmpFileName)



    expectedToPass = len(cppFiles)-missingExpected
    print('-----------------------------------------------------------------')
    print(f'Tests passed: {filesPassed}/{expectedToPass}')

    print(f'Insights crashed: {crashes}')
    print(f'Missing expected files: {missingExpected}')

    passed = (0 == missingExpected) and (expectedToPass == filesPassed)

    return (passed is False)  # note bash expects 0 for ok
#------------------------------------------------------------------------------


sys.exit(main())
#------------------------------------------------------------------------------

