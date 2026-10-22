-- TPC-H Q5 rewritten so DuckDB plans the Theseus shape: nation x region first, customers
-- semi-joined to the ASIA nations, orders x lineitem, a partial aggregate on (l_suppkey,
-- c_nationkey) before the supplier join, then nation names. Same answer as q5.sql.
-- Subqueries, not CTEs: the distributed planner refuses CTEs.
select n_name, revenue
from (
  select ol.c_nationkey, sum(ol.rev) as revenue
  from (
    select l_suppkey, c_nationkey, sum(l_extendedprice * (1 - l_discount)) as rev
    from orders, lineitem, (
      select c_custkey, c_nationkey from customer
      where c_nationkey in (select n_nationkey from nation, region
                            where n_regionkey = r_regionkey and r_name = 'ASIA')
    ) cust
    where o_custkey = c_custkey and l_orderkey = o_orderkey
      and o_orderdate >= date '1994-01-01' and o_orderdate < date '1995-01-01'
    group by l_suppkey, c_nationkey
  ) ol, supplier
  where ol.l_suppkey = s_suppkey and ol.c_nationkey = s_nationkey
  group by ol.c_nationkey
) by_nation, nation
where by_nation.c_nationkey = n_nationkey
order by revenue desc;
