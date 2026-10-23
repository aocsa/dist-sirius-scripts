create view customer as select * from read_parquet('/scratch/sirius/datasets/tpch_sf10000/customer/*.parquet');
create view orders as select * from read_parquet('/scratch/sirius/datasets/tpch_sf10000/orders/*.parquet');
create view lineitem as select * from read_parquet('/scratch/sirius/datasets/tpch_sf10000/lineitem/*.parquet');
create view supplier as select * from read_parquet('/scratch/sirius/datasets/tpch_sf10000/supplier/*.parquet');
create view nation as select * from read_parquet('/scratch/sirius/datasets/tpch_sf10000/nation/*.parquet');
create view region as select * from read_parquet('/scratch/sirius/datasets/tpch_sf10000/region/*.parquet');
.timer on
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
