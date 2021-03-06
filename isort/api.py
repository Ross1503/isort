import re
import textwrap
from io import StringIO
from itertools import chain
from pathlib import Path
from typing import Any, List, NamedTuple, Optional, TextIO, Tuple

from . import output, parse
from .exceptions import (
    ExistingSyntaxErrors,
    FileSkipComment,
    FileSkipSetting,
    IntroducedSyntaxErrors,
    UnableToDetermineEncoding,
)
from .format import format_natural, remove_whitespace, show_unified_diff
from .io import File
from .settings import DEFAULT_CONFIG, FILE_SKIP_COMMENTS, Config

IMPORT_START_IDENTIFIERS = ("from ", "from.import", "import ", "import*")
COMMENT_INDICATORS = ('"""', "'''", "'", '"', "#")


def _config(
    path: Optional[Path] = None, config: Config = DEFAULT_CONFIG, **config_kwargs
) -> Config:
    if path:
        if (
            config is DEFAULT_CONFIG
            and "settings_path" not in config_kwargs
            and "settings_file" not in config_kwargs
        ):
            config_kwargs["settings_path"] = path

    if config_kwargs and config is not DEFAULT_CONFIG:
        raise ValueError(
            "You can either specify custom configuration options using kwargs or "
            "passing in a Config object. Not Both!"
        )
    elif config_kwargs:
        config = Config(**config_kwargs)

    return config


def sorted_imports(
    file_contents: str,
    extension: str = "py",
    config: Config = DEFAULT_CONFIG,
    file_path: Optional[Path] = None,
    disregard_skip: bool = False,
    **config_kwargs,
) -> str:
    config = _config(config=config, **config_kwargs)
    content_source = str(file_path or "Passed in content")
    if not disregard_skip:
        if file_path and config.is_skipped(file_path):
            raise FileSkipSetting(content_source)

        for file_skip_comment in FILE_SKIP_COMMENTS:
            if file_skip_comment in file_contents:
                raise FileSkipComment(content_source)

    if config.atomic:
        try:
            compile(file_contents, content_source, "exec", 0, 1)
        except SyntaxError:
            raise ExistingSyntaxErrors(content_source)

    parsed_output = StringIO()
    sort_imports(StringIO(file_contents), parsed_output, extension=extension, config=config)
    parsed_output.seek(0)
    parsed_output = parsed_output.read()
    if config.atomic:
        try:
            compile(file_contents, content_source, "exec", 0, 1)
        except SyntaxError:
            raise IntroducedSyntaxErrors(content_source)
    return parsed_output


def check_imports(
    file_contents: str,
    show_diff: bool = False,
    extension: str = "py",
    config: Config = DEFAULT_CONFIG,
    file_path: Optional[Path] = None,
    disregard_skip: bool = False,
    **config_kwargs,
) -> bool:
    config = _config(config=config, **config_kwargs)

    sorted_output = sorted_imports(
        file_contents=file_contents,
        extension=extension,
        config=config,
        file_path=file_path,
        disregard_skip=disregard_skip,
        **config_kwargs,
    )
    if config.ignore_whitespace:
        line_separator = config.line_ending or parse._infer_line_separator(file_contents)
        compare_in = remove_whitespace(file_contents, line_separator=line_separator).strip()
        compare_out = remove_whitespace(sorted_output, line_separator=line_separator).strip()
    else:
        compare_in = file_contents.strip()
        compare_out = sorted_output.strip()

    if compare_out == compare_in:
        if config.verbose:
            print(f"SUCCESS: {file_path or ''} Everything Looks Good!")
        return True
    else:
        print(f"ERROR: {file_path or ''} Imports are incorrectly sorted.")
        if show_diff:
            show_unified_diff(
                file_input=file_contents, file_output=sorted_output, file_path=file_path
            )
        return False


def sorted_file(filename: str, config: Config = DEFAULT_CONFIG, **config_kwargs) -> str:
    file_data = File.read(filename)
    config = _config(path=file_data.path.parent, config=config)
    return sorted_imports(
        file_contents=file_data.contents,
        extension=file_data.extension,
        config=config,
        file_path=file_data.path,
        **config_kwargs,
    )


def sort_imports(
    input_stream: TextIO,
    output_stream: TextIO,
    extension: str = "py",
    config: Config = DEFAULT_CONFIG,
) -> None:
    """Parses stream identifying sections of contiguous imports and sorting them

    Code with unsorted imports is read from the provided `input_stream`, sorted and then
    outputted to the specified output_stream.

    - `input_stream`: Text stream with unsorted import sections.
    - `output_stream`: Text stream to output sorted inputs into.
    - `config`: Config settings to use when sorting imports. Defaults settings.DEFAULT_CONFIG.
    """
    line_separator: str = config.line_ending
    add_imports: List[str] = [format_natural(addition) for addition in config.add_imports]
    import_section: str = ""
    in_quote: str = ""
    first_comment_index_start: int = -1
    first_comment_index_end: int = -1
    contains_imports: bool = False
    in_top_comment: bool = False
    first_import_section: bool = True
    section_comments = [f"# {heading}" for heading in config.import_headings.values()]
    indent: str = ""
    isort_off: bool = False

    for index, line in enumerate(chain(input_stream, (None,))):
        if line is None:
            if index == 0 and not config.force_adds:
                return

            not_imports = True
            line = ""
            if not line_separator:
                line_separator = "\n"
        else:
            if not line_separator:
                line_separator = line[-1]

            stripped_line = line.strip()
            if (
                (index == 0 or (index == 1 and not contains_imports))
                and stripped_line.startswith("#")
                and stripped_line not in section_comments
            ):
                in_top_comment = True
            elif in_top_comment:
                if not line.startswith("#") or stripped_line in section_comments:
                    in_top_comment = False
                    first_comment_index_end = index - 1

            if (not stripped_line.startswith("#") or in_quote) and '"' in line or "'" in line:
                char_index = 0
                if first_comment_index_start == -1 and (
                    line.startswith('"') or line.startswith("'")
                ):
                    first_comment_index_start = index
                while char_index < len(line):
                    if line[char_index] == "\\":
                        char_index += 1
                    elif in_quote:
                        if line[char_index : char_index + len(in_quote)] == in_quote:
                            in_quote = ""
                            if first_comment_index_end < first_comment_index_start:
                                first_comment_index_end = index
                    elif line[char_index] in ("'", '"'):
                        long_quote = line[char_index : char_index + 3]
                        if long_quote in ('"""', "'''"):
                            in_quote = long_quote
                            char_index += 2
                        else:
                            in_quote = line[char_index]
                    elif line[char_index] == "#":
                        break
                    char_index += 1

            not_imports = bool(in_quote) or in_top_comment or isort_off
            if not (in_quote or in_top_comment):
                stripped_line = line.strip()
                if isort_off:
                    if stripped_line == "# isort: on":
                        isort_off = False
                elif stripped_line == "# isort: off":
                    not_imports = True
                    isort_off = True
                elif stripped_line == "# isort: split":
                    not_imports = True
                elif not stripped_line or stripped_line.startswith("#"):
                    import_section += line
                elif stripped_line.startswith(IMPORT_START_IDENTIFIERS):
                    contains_imports = True

                    indent = line[: -len(line.lstrip())]
                    import_section += line
                    while stripped_line.endswith("\\") or (
                        "(" in stripped_line and ")" not in stripped_line
                    ):
                        if stripped_line.endswith("\\"):
                            while stripped_line and stripped_line.endswith("\\"):
                                line = input_stream.readline()
                                stripped_line = line.strip().split("#")[0]
                                import_section += line
                        else:
                            while ")" not in stripped_line:
                                line = input_stream.readline()
                                stripped_line = line.strip().split("#")[0]
                                import_section += line
                else:
                    not_imports = True

        if not_imports:
            if (
                add_imports
                and not in_top_comment
                and not in_quote
                and not import_section
                and not line.lstrip().startswith(COMMENT_INDICATORS)
            ):
                import_section = line_separator.join(add_imports) + line_separator
                contains_imports = True
                add_imports = []

            if import_section:
                if add_imports and not indent:
                    import_section += line_separator.join(add_imports) + line_separator
                    contains_imports = True
                    add_imports = []

                if not indent:
                    import_section += line
                if not contains_imports:
                    output_stream.write(import_section)
                else:
                    if first_import_section and not import_section.lstrip(
                        line_separator
                    ).startswith(COMMENT_INDICATORS):
                        import_section = import_section.lstrip(line_separator)
                        first_import_section = False

                    if indent:
                        import_section = line_separator.join(
                            line.lstrip() for line in import_section.split(line_separator)
                        )
                    sorted_import_section = output.sorted_imports(
                        parse.file_contents(import_section, config=config), config, extension
                    )
                    if indent:
                        sorted_import_section = (
                            textwrap.indent(sorted_import_section, indent) + line_separator
                        )

                    output_stream.write(sorted_import_section)

                if indent:
                    output_stream.write(line)
                    indent = ""

                contains_imports = False
                import_section = ""
            else:
                output_stream.write(line)
                not_imports = False
