"""Paths and the one database of the child system."""
import json
import os
import re
import sqlite3
import time
from pathlib import Path

HOME = Path(os.environ.get("RUU_CHILD_HOME", "/data/ruu-child"))
CODE = Path(__file__).resolve().parent
ITEMS, RUNS, DB = HOME / "items", HOME / "runs", HOME / "child.sqlite3"


def open_db():
    db = sqlite3.connect(DB, timeout=30)
    db.row_factory = sqlite3.Row
    db.executescript("""
        create table if not exists runs(
            run_id text primary key, created real, model text, revision text, adapter text, parent text,
            item_set text, split text, spec text, meta text, status text default 'prepared');
        create table if not exists results(
            run_id text, item_id text, family text, grp text, sample integer, score real, correct integer,
            answered integer, tokens integer, finish text, primary key(run_id, item_id, sample));
        create table if not exists jobs(
            id integer primary key, run_id text, session text, gpu text, started real, deadline real,
            ended real, status text, units_before real, units_after real, note text);
        create table if not exists tunes(
            tune_id text primary key, created real, model text, revision text, source_run text, policy text,
            examples integer, spec text, meta text, status text default 'prepared');
    """)
    return db


def jsonl(path):
    with open(path, encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def allow():
    """What a job may ask for. Root-owned, so nothing that runs as ruu-child can widen it."""
    return json.loads((CODE / "allow.json").read_text(encoding="utf-8"))


def new_run_id(model):
    stamp = time.strftime("%m%d-%H%M%S", time.gmtime()) + os.urandom(1).hex()  # two runs in one second must differ
    return stamp + "-" + model.split("/")[-1].lower().replace(".", "")[:18]


THINK = re.compile(r"<think>.*?(</think>|$)", re.S)


def answer_region(text, thinking):
    """The part of a reply that counts as the answer. Reasoning is not the answer: a model that was
    still thinking when it ran out of tokens has not answered, whatever its notes happened to say."""
    text = text or ""
    if "</think>" in text:
        return text.rsplit("</think>", 1)[1]
    return "" if thinking else THINK.sub(" ", text)
