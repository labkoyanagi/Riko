# -*- coding: utf-8 -*-
"""Unit tests for the App-Gen script utilities."""

import os
import shutil
import tempfile
import unittest

import scripts.app_gen as app_gen


class AppGenRenderTests(unittest.TestCase):

    def test_render_template_replaces_all_tokens(self):
        template = "*HEADING\n** Job: {{JOB_NAME}}\n*PARAM, NAME={{PARAM_NAME}}"
        params = {'JOB_NAME': 'TestJob', 'PARAM_NAME': 'Value'}
        rendered = app_gen.render_template(template, params)
        self.assertIn('Job: TestJob', rendered)
        self.assertIn('NAME=Value', rendered)

    def test_render_template_missing_token_raises(self):
        template = "*HEADING\n** Job: {{JOB_NAME}}"
        params = {}
        with self.assertRaises(KeyError):
            app_gen.render_template(template, params)


class AppGenNameTests(unittest.TestCase):

    def test_determine_job_name_uses_preferred_columns(self):
        params = {'job_name': 'Example_Job'}
        self.assertEqual(app_gen.determine_job_name(params, 0), 'Example_Job')

    def test_determine_job_name_falls_back_to_index(self):
        params = {}
        self.assertEqual(app_gen.determine_job_name(params, 1), 'case_002')

    def test_sanitize_job_name_replaces_invalid_characters(self):
        self.assertEqual(app_gen.sanitize_job_name('My Job!*'), 'My_Job_')


class AppGenFileTests(unittest.TestCase):

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.temp_dir)

    def test_write_job_file_creates_directory_and_writes(self):
        output_dir = os.path.join(self.temp_dir, 'jobs')
        path = app_gen.write_job_file(output_dir, 'case_001', 'content')
        self.assertTrue(os.path.isfile(path))
        with open(path, 'r') as handle:
            self.assertEqual(handle.read(), 'content')


if __name__ == '__main__':
    unittest.main()
