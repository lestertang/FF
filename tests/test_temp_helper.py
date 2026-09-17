import os.path
import tempfile

import pytest

from facefusion import state_manager
from facefusion.download import conditional_download
from facefusion.temp_helper import get_temp_directory_path, get_temp_file_path, get_temp_frame_pattern
from .helper import get_test_example_file, get_test_examples_directory


@pytest.fixture(scope = 'module', autouse = True)
def before_all() -> None:
	conditional_download(get_test_examples_directory(),
	[
		'https://github.com/facefusion/facefusion-assets/releases/download/examples-3.0.0/target-240p.mp4'
	])
	state_manager.init_item('temp_path', tempfile.gettempdir())
	state_manager.init_item('temp_frame_format', 'png')


def test_get_temp_file_path() -> None:
	temp_directory_path = get_temp_directory_path(get_test_example_file('target-240p.mp4'))
	assert get_temp_file_path(get_test_example_file('target-240p.mp4')) == os.path.join(temp_directory_path, 'temp.mp4')


def test_get_temp_directory_path() -> None:
	temp_directory = tempfile.gettempdir()
	temp_directory_path = get_temp_directory_path(get_test_example_file('target-240p.mp4'))

	assert os.path.dirname(temp_directory_path) == os.path.join(temp_directory, 'facefusion')
	assert os.path.basename(temp_directory_path).startswith('target-240p-')
	assert get_temp_directory_path(os.path.join('first', 'target-240p.mp4')) != get_temp_directory_path(os.path.join('second', 'target-240p.mp4'))


def test_get_temp_frame_pattern() -> None:
	temp_directory_path = get_temp_directory_path(get_test_example_file('target-240p.mp4'))
	assert get_temp_frame_pattern(get_test_example_file('target-240p.mp4'), '%04d') == os.path.join(temp_directory_path, '%04d.png')
