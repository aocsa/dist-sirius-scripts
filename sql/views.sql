create view customer as select * from read_parquet('@DATA@/customer/*.parquet');
create view orders as select * from read_parquet('@DATA@/orders/*.parquet');
create view lineitem as select * from read_parquet('@DATA@/lineitem/*.parquet');
create view supplier as select * from read_parquet('@DATA@/supplier/*.parquet');
create view nation as select * from read_parquet('@DATA@/nation/*.parquet');
create view region as select * from read_parquet('@DATA@/region/*.parquet');
