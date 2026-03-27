# ---
# jupyter:
#   jupytext:
#     formats: ipynb,py:nomarker
#     text_representation:
#       extension: .py
#       format_name: nomarker
#       format_version: '1.0'
#     jupytext_version: 1.17.0
#   kernelspec:
#     display_name: Python 3 (ipykernel)
#     language: python
#     name: python3
# ---
"""Helper script to inspect and extract stress data from an Abaqus ODB file.

This utility is designed to be launched via ``abaqus python`` so that the
``odbAccess`` module is available. It supports two modes:

``inspect``
    Print a JSON payload describing the assembly instances contained in the ODB.

``extract``
    Export stress time series for specified element labels within one or more
    assembly instances. The output CSV contains the averaged stress components
    for each element at every analysis frame.
"""
from __future__ import print_function

import argparse
import csv
import json
import os
import sys

_COMPONENT_ORDER = ["S11", "S22", "S33", "S12", "S13", "S23"]

try:  # Python 2 compatibility shim
    unicode  # type: ignore[name-defined]
except NameError:  # pragma: no cover - executed on Python 3
    unicode = str  # type: ignore[assignment]


def _normalize_targets(target_specs):
    targets = {}
    if not target_specs:
        return targets
    for spec in target_specs:
        if spec is None:
            continue
        prefix_and_labels = spec.split(":", 1)
        if len(prefix_and_labels) != 2:
            continue
        prefix = prefix_and_labels[0].strip()
        labels_text = prefix_and_labels[1]
        if not prefix:
            continue
        if "|" in prefix:
            instance, part_name = prefix.split("|", 1)
        else:
            instance, part_name = prefix, None
        instance = instance.strip()
        if not instance:
            continue
        if part_name is not None:
            part_name = part_name.strip()
        labels = []
        for token in labels_text.replace("\n", " ").replace("\t", " ").split():
            token = token.strip().strip(",")
            if not token:
                continue
            try:
                labels.append(int(token))
            except ValueError:
                continue
        if labels:
            current = targets.get(instance)
            if current is None:
                targets[instance] = {"part": part_name, "labels": set(labels)}
            else:
                if part_name:
                    existing_part = current.get("part")
                    if existing_part and existing_part != part_name:
                        raise SystemExit(
                            "Conflicting part names supplied for instance %s" % instance
                        )
                    if not existing_part:
                        current["part"] = part_name
                current["labels"].update(labels)
    return targets


def _resolve_step(odb, step_argument):
    if not odb.steps:
        raise ValueError("The ODB contains no steps.")
    if not step_argument:
        return list(odb.steps.items())

    if step_argument.isdigit():
        index = int(step_argument)
        names = list(odb.steps.keys())
        if index < 0 or index >= len(names):
            raise ValueError("Step index %d is out of range." % index)
        name = names[index]
        return [(name, odb.steps[name])]

    if step_argument in odb.steps:
        return [(step_argument, odb.steps[step_argument])]

    raise ValueError("Step %s was not found in the ODB." % step_argument)


def _average(values):
    if not values:
        return None
    count = float(len(values))
    totals = [0.0] * len(values[0])
    for item in values:
        for idx, component_value in enumerate(item):
            totals[idx] += component_value
    return [component_sum / count for component_sum in totals]


def _get_field_output(frame, name):
    outputs = getattr(frame, "fieldOutputs", None)
    if outputs is None:
        return None

    getter = getattr(outputs, "get", None)
    if callable(getter):
        try:
            return getter(name)
        except Exception:
            return None
    try:
        return outputs[name]
    except Exception:
        pass

    has_key = getattr(outputs, "has_key", None)
    if callable(has_key):
        try:
            if has_key(name):  # type: ignore[call-arg]
                return outputs[name]
        except Exception:
            return None

    keys = getattr(outputs, "keys", None)
    if callable(keys):
        try:
            for key in list(keys()):
                if key == name:
                    return outputs[key]
        except Exception:
            return None

    return None


def _write_csv(path, rows):
    dirname = os.path.dirname(path)
    if dirname and not os.path.exists(dirname):
        os.makedirs(dirname)

    headers = [
        "job",
        "instance",
        "part",
        "element",
        "step",
        "frame",
        "increment",
        "time",
    ] + _COMPONENT_ORDER

    py3 = sys.version_info[0] >= 3
    if py3:
        handle = open(path, "w", newline="", encoding="utf-8")
    else:  # pragma: no cover - Python 2 handling for Abaqus
        handle = open(path, "wb")

    try:
        writer = csv.DictWriter(handle, fieldnames=headers)
        writer.writeheader()
        for row in rows:
            if py3:
                writer.writerow(row)
            else:  # pragma: no cover - Python 2 handling for Abaqus
                encoded = {}
                for key in headers:
                    value = row.get(key, "")
                    if isinstance(value, unicode):  # type: ignore[name-defined]
                        encoded[key] = value.encode("utf-8")
                    else:
                        encoded[key] = value
                writer.writerow(encoded)
    finally:
        handle.close()


def _inspect(odb_path):
    open_odb = _import_odb_access()
    odb = open_odb(path=str(odb_path), readOnly=True)
    try:
        instances = []
        assembly = odb.rootAssembly
        for name in sorted(assembly.instances.keys()):
            instance = assembly.instances[name]
            element_count = len(instance.elements)
            instances.append(
                {
                    "name": name,
                    "elementCount": element_count,
                    "partName": getattr(instance, "partName", ""),
                }
            )
        payload = {"instances": instances}
        print(json.dumps(payload))
    finally:
        odb.close()


def _extract(odb_path, job_name, targets, output_path, step_argument):
    if not targets:
        raise SystemExit("No valid targets were supplied.")

    open_odb = _import_odb_access()
    odb = open_odb(path=str(odb_path), readOnly=True)
    try:
        assembly = odb.rootAssembly
        resolved_targets = {}
        for instance_name, details in targets.items():
            if instance_name not in assembly.instances:
                raise SystemExit("Instance %s does not exist in the ODB." % instance_name)
            instance = assembly.instances[instance_name]
            requested_part = details.get("part")
            actual_part = getattr(instance, "partName", "")
            if requested_part and requested_part != actual_part:
                raise SystemExit(
                    "Instance %s belongs to part %s, but %s was requested"
                    % (instance_name, actual_part, requested_part)
                )
            label_list = sorted(details.get("labels", []))
            if not label_list:
                continue
            resolved_targets[instance_name] = {
                "part": actual_part,
                "labels": label_list,
            }

        steps = _resolve_step(odb, step_argument)
        rows = []
        for step_name, step in steps:
            frames = list(step.frames)
            for frame_index, frame in enumerate(frames):
                stress_field = _get_field_output(frame, "S")
                if stress_field is None:
                    continue

                per_element = {}
                for field_value in stress_field.values:
                    instance_name = getattr(field_value, "instanceName", None)
                    if instance_name is None:
                        instance_ref = getattr(field_value, "instance", None)
                        if instance_ref is not None:
                            instance_name = getattr(instance_ref, "name", None)
                    if instance_name is None:
                        continue

                    if instance_name not in resolved_targets:
                        continue

                    element_label = field_value.elementLabel
                    if element_label not in resolved_targets[instance_name]["labels"]:
                        continue

                    per_element.setdefault((instance_name, element_label), []).append(field_value.data)

                if not per_element:
                    continue

                increment_number = getattr(frame, "incrementNumber", None)
                time_value = getattr(frame, "frameValue", None)
                for key, values in per_element.items():
                    instance_name, element_label = key
                    averaged = _average(values)
                    if averaged is None:
                        continue

                    row = {
                        "job": job_name,
                        "instance": instance_name,
                        "part": resolved_targets[instance_name]["part"],
                        "element": element_label,
                        "step": step_name,
                        "frame": frame_index,
                        "increment": increment_number,
                        "time": time_value,
                    }
                    for idx, component_name in enumerate(_COMPONENT_ORDER):
                        if idx < len(averaged):
                            row[component_name] = averaged[idx]
                        else:
                            row[component_name] = ""
                    rows.append(row)

        _write_csv(output_path, rows)
    finally:
        odb.close()


def _import_odb_access():  # pragma: no cover - depends on Abaqus installation
    try:
        from odbAccess import openOdb  # type: ignore
    except Exception as exc:
        raise SystemExit("Failed to import odbAccess: %s" % (exc,))
    return openOdb


def _build_parser():
    parser = argparse.ArgumentParser(description="Inspect or extract data from an Abaqus ODB file.")
    parser.add_argument("--mode", choices={"inspect", "extract"}, required=True)
    parser.add_argument("--odb", required=True, help="Path to the ODB file")
    parser.add_argument("--job", help="Job name associated with the ODB (extract mode)")
    parser.add_argument("--out", help="Output CSV path (extract mode)")
    parser.add_argument("--component", help="Stress component to extract (extract mode)")
    parser.add_argument("--target", action="append", help="Instance[:part]: element numbers", default=[])
    parser.add_argument("--step", help="Step name or index", default=None)
    return parser


def main():
    parser = _build_parser()
    args = parser.parse_args()

    odb_path = os.path.abspath(args.odb)
    if not os.path.exists(odb_path):
        raise SystemExit("ODB file not found: %s" % odb_path)

    mode = args.mode
    if mode == "inspect":
        _inspect(odb_path)
        return

    if mode == "extract":
        if not args.job:
            raise SystemExit("--job is required for extract mode")
        if not args.out:
            raise SystemExit("--out is required for extract mode")

        targets = _normalize_targets(args.target)
        _extract(
            odb_path=odb_path,
            job_name=args.job,
            targets=targets,
            output_path=os.path.abspath(args.out),
            step_argument=args.step,
        )
        return

    raise SystemExit("Unsupported mode: %s" % mode)


if __name__ == "__main__":
    main()
