-- Local SQL Server only (Azure SQL Database is created by Bicep). Run against master.
IF DB_ID(N'rul') IS NULL
    CREATE DATABASE rul;
GO
