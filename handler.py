"""Runpod serverless worker for FaceFusion.

Queue-style endpoint that processes a target image or video through the
FaceFusion CLI and returns the URL of the output file.

Input contract:
{
	"input": {
		"target": "<http url | data uri | base64 of an image or video>",
		"source": "<http url | data uri | base64 of a source face/audio>",
		"source": ["<...>", "<...>"],
		"output": "<optional object name used for upload>",
		"cli_args": ["--face-detector-model", "yolo_face"],
		"<any facefusion cli flag as snake_case key>": "<value>"
	}
}

Any input key other than target, source, output and cli_args is converted to
a --kebab-case CLI flag and appended to the headless-run command. Boolean
values are emitted as bare flags, list values are repeated per item.

Output contract:
{
	"image": { "url": "https://..." },
	"video": { "url": "https://..." }
}

Errors are also returned as JSON with an "error" key; a non-zero FaceFusion
exit code raises a RuntimeError so the job fails with the traceback.
"""

import base64
import os
import shutil
import subprocess
import tempfile
import urllib.request
from typing import Any, Dict, List, Optional

import runpod
from runpod.serverless.utils import rp_cleanup, rp_upload

ROOT_PATH = os.path.dirname(os.path.abspath(__file__))
RESERVED_KEYS = [ 'target', 'source', 'output', 'cli_args' ]
IMAGE_EXTENSIONS = [ '.bmp', '.gif', '.jpeg', '.jpg', '.png', '.tiff', '.webp' ]


def handler(job : Dict[str, Any]) -> Dict[str, Any]:
	job_input : Dict[str, Any] = job.get('input', {})
	job_id : str = job.get('id')
	work_dir = tempfile.mkdtemp(prefix = 'facefusion-')
	output_path = ''

	try:
		target_path = resolve_input(job_input.get('target'), work_dir, 'target')
		source_paths = resolve_sources(job_input.get('source'), work_dir, 'source')
		output_path = os.path.join(work_dir, suggest_output_name(target_path, job_id))

		command = build_command(job_input, work_dir, target_path, source_paths, output_path)
		run_headless(command)

		return dispatch_output(output_path, job_input.get('output'))
	except Exception as exception:
		return { 'error': str(exception) }
	finally:
		if os.path.isfile(output_path):
			rp_cleanup.cleanup(output_path)
		if os.path.isdir(work_dir):
			shutil.rmtree(work_dir, ignore_errors = True)


def resolve_input(value : Any, work_dir : str, name : str) -> str:
	if not value:
		raise ValueError('missing required input: "' + name + '"')
	if isinstance(value, list):
		value = value[0]
	return materialize(value, work_dir, name)


def resolve_sources(value : Any, work_dir : str, name : str) -> List[str]:
	if not value:
		return []
	values = value if isinstance(value, list) else [ value ]
	return [ materialize(item, work_dir, name + '-' + str(index)) for index, item in enumerate(values) ]


def materialize(value : str, work_dir : str, name : str) -> str:
	if value.startswith('data:'):
		return materialize_data_uri(value, work_dir, name)
	if value.startswith('http://') or value.startswith('https://'):
		return download_url(value, work_dir, name)
	if looks_like_base64(value):
		return materialize_base64(value, work_dir, name)
	raise ValueError('unsupported input for "' + name + '": use an http(s) url, data uri or base64 string')


def materialize_data_uri(value : str, work_dir : str, name : str) -> str:
	header, _, content = value.partition(',')
	content_type = header.replace('data:', '').split(';')[0].split('/')[-1]
	extension = '.' + content_type if content_type else ''
	file_path = os.path.join(work_dir, name + extension)
	with open(file_path, 'wb') as file:
		file.write(base64.b64decode(content))
	return file_path


def materialize_base64(value : str, work_dir : str, name : str) -> str:
	file_path = os.path.join(work_dir, name)
	with open(file_path, 'wb') as file:
		file.write(base64.b64decode(value))
	return file_path


def download_url(value : str, work_dir : str, name : str) -> str:
	extension = os.path.splitext(urllib.request.urlparse(value).path)[1]
	file_path = os.path.join(work_dir, name + extension)
	urllib.request.urlretrieve(value, file_path)
	return file_path


def looks_like_base64(value : str) -> bool:
	if len(value) < 64 or len(value) % 4 != 0:
		return False
	if any(character not in 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/=' for character in value):
		return False
	return True


def suggest_output_name(target_path : str, job_id : str) -> str:
	target_name = os.path.splitext(os.path.basename(target_path))[0]
	target_extension = os.path.splitext(target_path)[1] or ('.png' if target_extension_is_image(target_path) else '.mp4')
	return target_name + (('_' + job_id) if job_id else '') + target_extension


def target_extension_is_image(target_path : str) -> bool:
	return os.path.splitext(target_path)[1].lower() in IMAGE_EXTENSIONS


def build_command(job_input : Dict[str, Any], work_dir : str, target_path : str, source_paths : List[str], output_path : str) -> List[str]:
	python = shutil.which('python3.10') or 'python3.10'
	command =\
	[
		python,
		os.path.join(ROOT_PATH, 'facefusion.py'),
		'headless-run',
		'--config-path', '/dev/null',
		'--temp-path', work_dir,
		'--jobs-path', os.path.join(work_dir, '.jobs'),
		'--log-level', 'error',
		'--target-path', target_path,
		'--output-path', output_path
	]
	for source_path in source_paths:
		command.extend([ '--source-paths', source_path ])
	command.extend(flatten_options(job_input))
	command.extend(str(argument) for argument in job_input.get('cli_args', []))
	return command


def flatten_options(job_input : Dict[str, Any]) -> List[str]:
	flags = []
	for key, value in job_input.items():
		if key in RESERVED_KEYS or value is None:
			continue
		flag = '--' + key.replace('_', '-')
		if isinstance(value, bool):
			if value:
				flags.append(flag)
			continue
		values = value if isinstance(value, list) else [ value ]
		for item in values:
			if item is not None:
				flags.extend([ flag, str(item) ])
	return flags


def run_headless(command : List[str]) -> None:
	process = subprocess.run(command, capture_output = True, text = True, check = False)
	if process.returncode != 0:
		raise RuntimeError('FaceFusion exited with code ' + str(process.returncode) + ': ' + (process.stderr or process.stdout))


def dispatch_output(output_path : str, to_upload : Optional[str]) -> Dict[str, Any]:
	if not os.path.isfile(output_path):
		raise RuntimeError('FaceFusion did not produce an output file')
	url = rp_upload(output_path, to_upload or '')
	if target_extension_is_image(output_path):
		return { 'image': { 'url': url } }
	return { 'video': { 'url': url } }


runpod.serverless.start({ 'handler': handler })
