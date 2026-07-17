import argparse
import json

from datetime import datetime
from hashlib import sha256
from pathlib import Path
from subprocess import run as procrun
from urllib.parse import quote as urlquote
from zipfile import ZipFile

from typing import Callable, Any

REPOSITORY = "https://github.com/ArchipelagoDoom/worlds"
BASEPATH = Path(".")
DESTPATH = Path(".")


def GetFromArchive(zipped: ZipFile, condition: Callable[[str], bool], allow_empty: bool = True) -> str:
    results = [f for f in zipped.namelist() if condition(f)]
    if len(results) == 0:
        if not allow_empty:
            raise RuntimeError("No matching files found")
        return None
    return results[0]


class WorldRevision:
    url: str
    size: int
    hash_sha256: str
    world_version: str
    ap_version: str | None
    date_time: datetime

    def __init__(self, path: Path, revision: str):
        # Check out the given revision of this world
        procrun(["git", "checkout", "-q", revision, "--", str(path)], capture_output=True, check=True)

        with open(path, "rb") as basefile:
            urlpath = urlquote(str(path.relative_to(BASEPATH)))
            self.url = f"{REPOSITORY}/raw/{revision}/{urlpath}"
            self.hash_sha256 = sha256(basefile.read()).hexdigest()
            self.size = basefile.tell()

        with ZipFile(path) as zippedfile:
            manifest = GetFromArchive(zippedfile, lambda f: f.endswith("archipelago.json"), allow_empty=False)
            with zippedfile.open(manifest) as manifile:
                manijson = json.loads(manifile.read().decode("utf-8"))
                self.world_version = manijson["world_version"]
                self.ap_version = manijson.get("minimum_ap_version", None)

            self.date_time = datetime(*zippedfile.getinfo(manifest).date_time)

    def as_indexed_world(self, world_id: str) -> dict[str, Any]:
        jsonblob = {
            "world": self.url,
            "hash_sha256": self.hash_sha256,
            "size": self.size,
            "metadata": {
                "game": "",
                "id": world_id,
                "world_version": self.world_version,
                "tag_version": self.world_version,
                "created_at": self.date_time.isoformat()
            }
        }
        if self.ap_version is not None:
            jsonblob["metadata"]["minimum_ap_version"] = self.ap_version
        return jsonblob

    def size_str(self) -> str:
        tmpsize = self.size
        for unit in ("bytes", "KB", "MB", "GB", "TB"):
            if tmpsize < 1000.0:
                if tmpsize == self.size:
                    return f"{tmpsize} {unit}"
                return f"{tmpsize:.1f} {unit}"
            tmpsize /= 1024.0
        return ">1 PB"


class WorldMeta:
    full_name: str
    short_name: str
    ap_name: str
    description: str
    authors: list[str]

    def __init__(self, path: Path):
        # Make sure HEAD version of this world is checked out, we want latest info
        procrun(["git", "checkout", "-q", "HEAD", "--", str(path)], capture_output=True, check=True)

        with ZipFile(path) as zippedfile:
            manifest = GetFromArchive(zippedfile, lambda f: f.endswith("archipelago.json"), allow_empty=False)
            with zippedfile.open(manifest) as manifile:
                manijson = json.loads(manifile.read().decode("utf-8"))
                self.ap_name = manijson["game"]
                self.full_name = manijson["__apdoom"].get("full_name", self.ap_name)
                self.short_name = manijson["__apdoom"]["short_name"]
                self.authors = manijson.get("authors", [])

            self.description = ""
            if pyworld := GetFromArchive(zippedfile, lambda f: f.endswith("__init__.py") and "id1common" not in f):
                docstring_searching = False
                docstring_indent = -1
                docstring_lines = []

                with zippedfile.open(pyworld) as f:
                    for line in f.read().decode("utf-8").splitlines():
                        if not docstring_searching:
                            if "class" in line and "id1CommonWorld" in line:
                                docstring_searching = True
                        else:
                            if '"""' in line:
                                if docstring_indent != -1:
                                    break
                                docstring_indent = line.find('"""')
                                docstring_lines.append(line[docstring_indent+3:])
                            elif docstring_indent != -1:
                                docstring_lines.append(line[docstring_indent:])
                self.description = "\n".join(docstring_lines).strip(" \t\n")

    def as_indexed_meta(self, world_id: str) -> dict[str, Any]:
        return {
            world_id: {
                "game": self.ap_name,
                "description": self.description,
                "release_url": f"https://archipelagodoom.github.io/worlds/#{self.short_name}",
                "authors": self.authors
            }
        }

    def authors_str(self) -> str:
        if len(self.authors) == 0:
            return "Unknown"
        if len(self.authors) == 1:
            return self.authors[0]
        if len(self.authors) == 2:
            return f"{self.authors[0]} & {self.authors[1]}"
        temp = self.authors.copy()
        temp[-1] = f"& {temp[-1]}"
        return ", ".join(temp)


class World:
    world_type: str
    world_id: str
    revisions: list[WorldRevision]
    meta: WorldMeta

    def __init__(self, path: Path):
        self.world_type = "Official" if str(path.parent).endswith("Official") else "User"
        self.world_id = path.stem

        procres = procrun(["git", "log", "--pretty=tformat:%h", "--", str(path)],
                          capture_output=True, text=True, check=True)
        self.revisions = [WorldRevision(path, rev) for rev in procres.stdout.splitlines()]
        self.meta = WorldMeta(path)

    def get_index_data(self) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        worlds = [revision.as_indexed_world(self.world_id) for revision in self.revisions]
        metadata = self.meta.as_indexed_meta(self.world_id)
        return (worlds, metadata)


def sort_worlds(path: Path) -> tuple[int, str]:
    # Official worlds to the top, then sort by stem
    return (-1 if str(path.parent).endswith("Official") else 0, path.stem)


def create_index_file(worlds: list[World]) -> None:
    index = {
        "index_version": 1,
        "worlds": [],
        "meta": {},
    }

    for w in worlds:
        worlds, meta = w.get_index_data()
        index["worlds"].extend(worlds)
        index["meta"].update(meta)

    with open(DESTPATH / "index.json", "w") as outputfile:
        outputfile.write(json.dumps(index, indent=2))
    print(f"Index file created at '{DESTPATH / 'index.json'}'.") 


def create_web_page(worlds: list[World]) -> None:
    with open(BASEPATH / "wadfiles.json", "r") as jsonfile:
        wadfiles = json.loads(jsonfile.read())
    with open(DESTPATH / "template.html", "r") as tempfile:
        template = tempfile.read()

    def get_wad_file(shortname: str) -> str:
        return "" if shortname not in wadfiles else f'<a href="{wadfiles[shortname]}">WAD Files</a> &horbar; ' 

    def format_rows(subworlds: list[World]) -> str:
        tablerow_templates = {
            "latest": '''<tr id="{short_name}">
              <td>{ap_name} <a class="section" href="#{short_name}">&#x1f517;</a>
                <div class="subtext">{wadfiles}Authors: {authors}</div></td>
              <td><a href="{url}">{version}</a>
                <div class="subtext">(Latest Version)</div></td>
              <td>{size}</td>
              <td class="hash" title="{sha256}">{sha256}</td>
              </tr>''',
            "history": '''<tr>
              <td></td>
              <td><a href="{url}">{version}</a></td>
              <td>{size}</td>
              <td class="hash" title="{sha256}">{sha256}</td>
              </tr>'''
        }

        rows = []
        for world in subworlds:
            first = True
            for revision in world.revisions:
                template = tablerow_templates["latest" if first else "history"]
                row = template.format(short_name=world.meta.short_name,
                                      ap_name=world.meta.ap_name,
                                      authors=world.meta.authors_str(),
                                      url=revision.url,
                                      version=revision.world_version,
                                      size=revision.size_str(),
                                      sha256=revision.hash_sha256,
                                      wadfiles=get_wad_file(world.meta.short_name))
                rows.append(row)
                first = False

        return "\n".join(rows)

    with open(DESTPATH / "index.html", "w") as outputfile:
        output = template.format(last_update=datetime.utcnow().strftime("%-d-%b-%Y %H:%M:%S UTC"),
                                 official_worlds=format_rows([w for w in worlds if w.world_type == "Official"]),
                                 user_worlds=format_rows([w for w in worlds if w.world_type != "Official"]))
        outputfile.write(output)
    print(f"HTML file created at '{DESTPATH / 'index.html'}'.") 


if __name__ == "__main__":
    argp = argparse.ArgumentParser(description="Build APDoom world website and index.")
    argp.add_argument('source', help="Path for worlds.")
    argp.add_argument('dest', help="Destination path for output files.")
    arguments = argp.parse_args()

    BASEPATH = Path(arguments.source)
    DESTPATH = Path(arguments.dest)

    worlds = sorted([World(worldpath) for worldpath in BASEPATH.rglob("*.apworld")], key=lambda k: k.world_id)

    create_index_file(worlds)
    create_web_page(worlds)

