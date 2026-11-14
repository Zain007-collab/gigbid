# sales-dash-non-profit-9 (synthetic)

=XLOOKUP(A2, Orders!A:A, Orders!F:F)

Total Sales = SUM(Sales[Amount])  -- DAX measure

ALTER WAREHOUSE reporting SET AUTO_SUSPEND = 60;

SELECT customer_id, SUM(amount) FROM orders GROUP BY 1
