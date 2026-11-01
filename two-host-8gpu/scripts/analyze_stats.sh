#!/usr/bin/env bash
# Write ../stats/<basename DATA>.csv: exact min/max and null count of every integer, decimal and date
# column of each table under DATA, read from the parquet footers. gen_sql.py hands them to the views
# with STATS=1. A multi-file read_parquet has no column statistics of its own, so without them
# DuckDB takes a filtered table's row count as its join keys' domain and misjudges the joins it
# feeds, and Sirius cannot bound DECIMAL sums to 64 bits.
# Do not run it next to a benchmark: it reads every footer under DATA.
#
# usage: analyze_stats.sh DATA [OUT]
set -euo pipefail
data=${1:?usage: analyze_stats.sh DATA [OUT]}
here=$(cd "$(dirname "$0")" && pwd)
out=${2:-$here/../stats/$(basename "$data").csv}
duckdb=${DUCKDB:-${WT:-/scratch/prestouser/aocsa/wt/xnode-staging}/sirius-duckdb/build/release/duckdb}
unset AI_AGENT CLAUDECODE CLAUDE_CODE CURSOR_AGENT
mkdir -p "$(dirname "$out")"
{
  echo "table,column,min,max,null_count"
  for t in nation region supplier customer part partsupp orders lineitem; do
    [ -d "$data/$t" ] || continue
    files="$data/$t/*.parquet"
    # One select per column over a single pass of the footers.
    selects=$("$duckdb" -no-agent -list -noheader -c "
      select string_agg(format(
        'select ''$t'', ''{0}'', min(stats_min_value::{1})::varchar, max(stats_max_value::{1})::varchar, sum(stats_null_count) from m where path_in_schema = ''{0}''',
        column_name, column_type), ' union all '),
        string_agg(format('''{0}''', column_name), ', ')
      from (describe select * from read_parquet('$files'))
      where column_type in ('TINYINT', 'SMALLINT', 'INTEGER', 'BIGINT', 'DATE')
         or column_type like 'DECIMAL%'")
    columns=${selects##*|}
    "$duckdb" -no-agent -csv -noheader -c "set threads = 8;
      with m as materialized (
        select path_in_schema, stats_min_value, stats_max_value, stats_null_count
        from parquet_metadata('$files') where path_in_schema in ($columns))
      ${selects%|*} order by 2"
  done
} > "$out.tmp"
mv "$out.tmp" "$out"
echo "$out"
