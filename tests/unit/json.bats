#!/usr/bin/env bats
# tests/unit/json.bats - lib/omes/json.sh unit tests.
#
# Validity is proven with `python3 -c "import json; json.loads(...)"` per
# the testing conventions in the brief (no jq dependency in the library).

setup() {
  load '../test_helper.bash'
  omes_test_setup
  # shellcheck source=../../lib/omes/json.sh
  source "${OMES_TEST_ROOT}/lib/omes/json.sh"
}

teardown() {
  omes_test_teardown
}

_assert_valid_json() {
  python3 -c 'import json,sys; json.loads(sys.stdin.read())' <<<"$1"
}

@test "json_escape escapes backslashes and quotes" {
  run json_escape 'a\b"c'
  [ "$status" -eq 0 ]
  [ "$output" = 'a\\b\"c' ]
}

@test "json_escape escapes newlines, tabs and carriage returns" {
  local input
  input="$(printf 'line1\nline2\ttabbed\r')"
  run json_escape "$input"
  [ "$status" -eq 0 ]
  [ "$output" = 'line1\nline2\ttabbed\r' ]
}

@test "json_kv produces a quoted key/value pair" {
  run json_kv "name" "apt-base"
  [ "$status" -eq 0 ]
  [ "$output" = '"name":"apt-base"' ]
}

@test "json_kv --raw emits the value unquoted" {
  run json_kv "ok" "true" --raw
  [ "$status" -eq 0 ]
  [ "$output" = '"ok":true' ]
}

@test "json_obj joins pairs into a valid object" {
  local obj
  obj="$(json_obj "$(json_kv name apt-base)" "$(json_kv ok true --raw)")"
  [ "$obj" = '{"name":"apt-base","ok":true}' ]
  _assert_valid_json "$obj"
}

@test "json_obj with no arguments is an empty object" {
  local obj
  obj="$(json_obj)"
  [ "$obj" = '{}' ]
  _assert_valid_json "$obj"
}

@test "json_array joins elements into a valid array" {
  local arr
  arr="$(json_array "$(json_obj "$(json_kv name a)")" "$(json_obj "$(json_kv name b)")")"
  [ "$arr" = '[{"name":"a"},{"name":"b"}]' ]
  _assert_valid_json "$arr"
}

@test "json values containing quotes and backslashes stay valid JSON" {
  local obj
  obj="$(json_obj "$(json_kv detail 'a "quoted" \path\ value')")"
  _assert_valid_json "$obj"
}

@test "a nested check-array document round-trips through python json.loads" {
  local checks doc
  checks="$(
    json_array \
      "$(json_obj "$(json_kv name os)" "$(json_kv ok true --raw)" "$(json_kv detail "ubuntu 24.04")")" \
      "$(json_obj "$(json_kv name arch)" "$(json_kv ok false --raw)" "$(json_kv detail "unsupported")")"
  )"
  doc="$(json_obj "$(json_kv command check)" "$(json_kv ok false --raw)" "$(json_kv checks "$checks" --raw)" "$(json_kv exit_code 3 --raw)")"
  _assert_valid_json "$doc"
  run python3 -c "import json,sys; d=json.loads(sys.stdin.read()); print(len(d['checks'])); print(d['checks'][1]['ok'])" <<<"$doc"
  [ "$status" -eq 0 ]
  [ "${lines[0]}" = "2" ]
  [ "${lines[1]}" = "False" ]
}
