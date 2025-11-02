# Riko Abaqus Automation Helpers

This repository collects helper scripts for managing Abaqus simulation workflows. The only implemented tool for now is **App-Gen**, which creates job-specific `.inp` files from a template and a CSV table of parameters.

## Prerequisites

- Python 2.7 (the scripts intentionally avoid Python 3 features).
- A shell or command prompt where you can run `python2`.

## Directory layout

```
Riko/
├── templates/   # Place your Abaqus template files here (e.g. model_template.inp)
├── params/      # Place your CSV parameter tables here (e.g. sweep.csv)
├── jobs/        # App-Gen writes generated .inp files into this folder
├── results/
├── extracts/
└── scripts/     # Contains app_gen.py
```

## How to try App-Gen

Follow these steps if you are brand new to the workflow. The example assumes you are already inside the project directory (`Riko/`).

1. **Prepare the template file.**
   - Create `templates/model_template.inp` with placeholder tokens such as `{{JOB_NAME}}`, `{{TARGET_ELSET}}`, etc. Tokens must match the column names in your CSV.
   - Example snippet:
     ```
     *HEADING
     ** Job name will be substituted
     **
     *EL PRINT, ELSET={{TARGET_ELSET}}, FREQUENCY=1
     S, {{JOB_NAME}}
     ```

2. **Create the parameter table.**
   - Create `params/sweep.csv` with a header row and one line per simulation case.
   - Example content (save as plain text):
     ```csv
     JOB_NAME,TARGET_ELSET
     demo_case_01,ELEMENT_SET_A
     demo_case_02,ELEMENT_SET_B
     ```

3. **Run App-Gen.**
   - On Linux/macOS: `python2 scripts/app_gen.py --template templates/model_template.inp --params params/sweep.csv --jobs-dir jobs`
   - On Windows (Command Prompt): `python scripts\app_gen.py --template templates\model_template.inp --params params\sweep.csv --jobs-dir jobs`

4. **Check the results.**
   - App-Gen prints progress to the terminal. If it says `Successfully generated 2 job file(s).`, the command worked.
   - Look inside the `jobs/` folder—you should see `demo_case_01.inp`, `demo_case_02.inp`, etc. Open them in a text editor to confirm the placeholders were replaced.

5. **Troubleshooting tips.**
   - If you see an error about "Missing parameters for tokens", make sure every `{{TOKEN}}` used in the template has a corresponding column in the CSV.
   - If the script cannot find a file, double-check the paths you passed to `--template`, `--params`, and `--jobs-dir`.

Once you are comfortable with App-Gen, you can adapt the template and CSV to your real Abaqus models. Future scripts (App-Run, App-Post, App-Flow) will build on the files generated here.
