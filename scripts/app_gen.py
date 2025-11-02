# -*- coding: utf-8 -*-
"""
App-Gen: Generate Abaqus input decks from a template and parameter sweep table.

Usage example:
    python2 scripts/app_gen.py \
        --template templates/model_template.inp \
        --params params/sweep.csv \
        --jobs-dir jobs

This script targets Python 2.7 and avoids f-strings by using str.format().
"""

from __future__ import print_function
import argparse
import csv
import logging
import os
import re
import sys


LOGGER = logging.getLogger(__name__)
TOKEN_PATTERN = re.compile(r"\{\{([A-Za-z0-9_]+)\}\}")
DEFAULT_NAME_CANDIDATES = [
    "job_name",
    "JOB_NAME",
    "JobName",
    "case",
    "CASE",
    "Case",
]


def configure_logging(verbose):
    """Configure root logging with a simple console handler."""
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format='[%(levelname)s] %(message)s'
    )


def load_template(path):
    """Read the template file and return its contents."""
    LOGGER.debug('Loading template from {0}'.format(path))
    with open(path, 'r') as handle:
        return handle.read()


def read_parameter_table(path):
    """Load CSV parameter table into a list of dictionaries."""
    LOGGER.debug('Reading parameter table from {0}'.format(path))
    with open(path, 'r') as handle:
        reader = csv.DictReader(handle)
        rows = [row for row in reader]
    if not rows:
        raise ValueError('Parameter table {0} is empty.'.format(path))
    LOGGER.info('Loaded {0} parameter sets from {1}'.format(len(rows), path))
    return rows


def render_template(template_text, parameters):
    """Replace {{TOKEN}} entries with values from the provided parameters."""
    missing_keys = []

    def replace(match):
        token = match.group(1)
        if token not in parameters or parameters[token] is None:
            missing_keys.append(token)
            return match.group(0)
        return str(parameters[token])

    rendered = TOKEN_PATTERN.sub(replace, template_text)
    if missing_keys:
        # Remove duplicates while preserving order.
        seen = set()
        ordered_missing = []
        for key in missing_keys:
            if key not in seen:
                ordered_missing.append(key)
                seen.add(key)
        raise KeyError('Missing parameters for tokens: {0}'.format(', '.join(ordered_missing)))
    return rendered


def ensure_directory(path):
    """Create the directory if it does not already exist."""
    if not os.path.isdir(path):
        LOGGER.debug('Creating directory {0}'.format(path))
        os.makedirs(path)


def determine_job_name(parameters, index):
    """Determine the output job name based on parameter values or fallback."""
    for key in DEFAULT_NAME_CANDIDATES:
        if key in parameters and parameters[key]:
            name = parameters[key].strip()
            if name:
                LOGGER.debug('Using parameter {0} value {1} as job name.'.format(key, name))
                return sanitize_job_name(name)
    fallback = 'case_{0:03d}'.format(index + 1)
    LOGGER.debug('Falling back to generated job name {0}'.format(fallback))
    return fallback


def sanitize_job_name(name):
    """Sanitize a job name to contain only safe filesystem characters."""
    safe = re.sub(r'[^A-Za-z0-9_-]+', '_', name)
    if not safe:
        safe = 'case_000'
    return safe


def write_job_file(output_dir, job_name, contents):
    """Write the rendered template to the target directory."""
    ensure_directory(output_dir)
    destination = os.path.join(output_dir, '{0}.inp'.format(job_name))
    LOGGER.debug('Writing job file {0}'.format(destination))
    with open(destination, 'w') as handle:
        handle.write(contents)
    return destination


def parse_arguments(argv):
    """Parse CLI arguments for the App-Gen script."""
    parser = argparse.ArgumentParser(description='Generate Abaqus input files from a template.')
    parser.add_argument('--template', required=True, help='Path to the Abaqus .inp template file.')
    parser.add_argument('--params', required=True, help='Path to the CSV parameter sweep table.')
    parser.add_argument('--jobs-dir', default='jobs', help='Directory to output generated .inp files.')
    parser.add_argument('--verbose', action='store_true', help='Enable debug logging output.')
    return parser.parse_args(argv)


def main(argv=None):
    """Entry point for command-line execution."""
    if argv is None:
        argv = sys.argv[1:]
    args = parse_arguments(argv)
    configure_logging(args.verbose)

    try:
        template_text = load_template(args.template)
        parameter_sets = read_parameter_table(args.params)
        generated_paths = []
        for index, parameters in enumerate(parameter_sets):
            job_name = determine_job_name(parameters, index)
            LOGGER.info('Rendering template for job {0}'.format(job_name))
            rendered = render_template(template_text, parameters)
            destination = write_job_file(args.jobs_dir, job_name, rendered)
            generated_paths.append(destination)
        LOGGER.info('Successfully generated {0} job file(s).'.format(len(generated_paths)))
        return 0
    except Exception as exc:
        LOGGER.error('Generation failed: {0}'.format(exc))
        return 1


if __name__ == '__main__':
    sys.exit(main())
