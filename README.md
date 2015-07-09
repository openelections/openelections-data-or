# OpenElections Data Oregon

This repository contains pre-processed election results from Oregon, formatted to be ingested into the OpenElections [processing pipeline](http://docs.openelections.net/guide/). It contains mostly CSV files converted from PDF tables. Interested in contributing? We have a bunch of [easy tasks](https://github.com/openelections/openelections-data-or/labels/easy%20task) for you to tackle.

Here is what a [finished CSV file (from Ohio)](https://github.com/openelections/openelections-data-oh/blob/master/2000/20001107__oh__general__president.csv) looks like. Note that each row represents a single result for a single candidate,even if the data has multiple candidates in a single row. Also, vote totals do not contain commas or other formatting.

For extracting text from PDF tables,we recommend [Tabula](http://tabula.technology/), which can be installed and run locally on OSX,Windows or Linux.

If you're familiar with git and Github,clone this repository and get started. If not, you can still help: leave a comment on a task you'd like to work on, or just convert any of the files into CSV and send the result to openelections@gmail.com.
