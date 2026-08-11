# TruCalc Orders

### Odoo Community 19.0 Appraisal & Evaluation Management System

**Version:** Baseline v1
**Repository:** https://github.com/tnfirstflight-art/trucalc_orders

---

# Project Overview

TruCalc Orders is a custom Odoo Community 19 module developed to provide an enterprise appraisal and evaluation management platform for financial institutions.

The long-term objective is to replace legacy appraisal ordering systems with a modern, multi-company workflow supporting:

* Evaluation Orders
* Commercial Appraisals
* Residential Appraisals
* Environmental Reports
* Review Assignments
* Vendor Management
* Bid Management
* Document Management
* Multi-bank support
* Audit Trail
* Banking compliance

The application is designed to be deployed for multiple financial institutions using Odoo's native multi-company architecture.

---

# Development Environment

## Operating System

macOS (Apple Silicon)

---

## Odoo Version

Odoo Community 19.0

Repository:

```
~/Projects/odoo
```

---

## Python

Python Virtual Environment

Activate before running Odoo:

```bash
cd ~/Projects/odoo
source venv/bin/activate
```

---

## PostgreSQL

**Supported Version**

PostgreSQL 15

> **Important:** PostgreSQL 17 is installed on the development machine but is **not** the development database for this project. PostgreSQL 15 contains the active TruCalc development database.

Before starting Odoo, verify PostgreSQL 15 is the active service.

---

# Development Database

Development database:

```
trucalc_dev
```

This is the authoritative development database.

Do **not** create replacement databases unless specifically instructed.

---

# Starting Odoo

Activate the virtual environment:

```bash
cd ~/Projects/odoo
source venv/bin/activate
```

Start Odoo:

```bash
python odoo-bin -c debian/odoo.conf
```

If necessary, update the module:

```bash
python odoo-bin -c debian/odoo.conf -u trucalc_orders
```

Open:

```
http://localhost:8069
```

---

# Module Location

```
~/Projects/odoo/custom_addons/trucalc_orders
```

---

# Repository Structure

```
trucalc_orders/

├── data/
├── models/
├── security/
├── views/

├── __manifest__.py
├── __init__.py
├── README.md
├── .gitignore
```

---

# Module Upgrade Procedure

Whenever models, security, XML views, or manifests are modified:

Activate the virtual environment:

```bash
source venv/bin/activate
```

Upgrade the module:

```bash
python odoo-bin -c debian/odoo.conf -u trucalc_orders
```

Review the Odoo console for:

* Python exceptions
* XML parsing errors
* Security errors
* Missing imports
* View inheritance errors

Resolve all errors before committing.

---

# Git Workflow

## Main Branch

```
main
```

---

## Development Process

Every feature shall follow this workflow:

```
Feature

↓

Implement

↓

Upgrade Module

↓

Test

↓

Commit

↓

Push
```

One feature per commit.

Do not combine unrelated changes into a single commit.

---

## Baseline Tag

```
baseline-v1
```

This tag represents the recovered development baseline.

Never rewrite history before this tag.

---

# Commit Guidelines

Commit messages should be concise and descriptive.

Examples:

```
Implement reviewer fee auto-population

Complete bid selection workflow

Fix vendor security rules

Add reviewer assignment action

Refactor evaluation order workflow
```

---

# Development Rules for Codex

Codex is expected to function as an implementation engineer, not as a software architect.

Before modifying any code, Codex shall first understand the entire repository.

## Required Rules

### 1. Never rewrite the application.

Enhance the existing architecture.

---

### 2. Preserve existing functionality.

Avoid regressions.

---

### 3. Work from complete files.

Do not generate partial snippets unless specifically requested.

When modifying a file, return the complete replacement file.

---

### 4. Make the smallest change necessary.

Avoid unnecessary refactoring.

---

### 5. Respect Odoo conventions.

Use Odoo Community 19 best practices.

---

### 6. Maintain compatibility.

Do not remove existing functionality unless explicitly instructed.

---

### 7. Preserve banking workflows.

The business workflow takes precedence over code elegance.

---

### 8. Verify dependencies.

Any modification affecting:

* XML
* Security
* Manifest
* Imports
* Models

must also update any dependent files.

---

### 9. Never invent requirements.

Implement only requested functionality.

---

### 10. Explain architectural changes.

If a redesign is recommended, explain:

* Why
* Benefits
* Risks
* Alternative approaches

before implementing.

---

# Testing Requirements

Every completed feature should be tested for:

* Module upgrade
* Form view loading
* Tree view loading
* Security permissions
* Multi-company isolation
* Chatter functionality
* Workflow transitions
* Related field calculations
* Record creation
* Record editing
* Record deletion (where applicable)

No feature is considered complete until testing passes.

---

# Long-Term Objectives

Target production features include:

* Vendor bidding
* Blind bid presentation
* Reviewer assignment
* Document management
* Invoice management
* Audit logging
* Automated notifications
* Bank-specific configuration
* Production security hardening
* Deployment to Bluehost VPS

---

# Design Philosophy

This project prioritizes:

1. Stability
2. Banking compliance
3. Maintainability
4. Readability
5. Production readiness

Code should always be understandable by another senior Odoo developer and suitable for deployment in a regulated financial institution.

---

# Current Project Status

Current status:

* Baseline recovered
* Git repository initialized
* GitHub repository established
* PostgreSQL development environment identified
* Ready for architecture review and controlled feature development

Future development should proceed incrementally, with each completed feature committed, tested, and pushed to GitHub before beginning the next.
