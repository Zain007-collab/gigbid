# tableau-exec-travel-7 (synthetic)

=XLOOKUP(A2, Orders!A:A, Orders!F:F)

ALTER WAREHOUSE reporting SET AUTO_SUSPEND = 60;

SELECT customer_id, SUM(amount) FROM orders GROUP BY 1

// Tableau calc: IF [Profit] < 0 THEN 'Loss' END
