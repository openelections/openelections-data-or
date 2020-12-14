library(tidyverse)
library(here)

columbia_folder <- here("..", "columbia")

columbia_files <- list.files(path = columbia_folder, 
                             pattern = ".csv",
                             full.names = TRUE)

cand_collect <- function(data) {
  data %>%
    mutate_all(as.character) %>%
    pivot_longer(-Precinct, names_to = "candidate", values_to = "votes")
}

columbia <- lapply(columbia_files, read_csv) %>%
  lapply(cand_collect) %>%
  magrittr::set_names(columbia_files) %>%
  bind_rows(.id = "file") %>%
  mutate(office = case_when(
           str_detect(file, "attorney_general.csv$") ~ "Attorney General",
           str_detect(file, "president.csv$") ~ "President",
           str_detect(file, "sec_state.csv$") ~ "Secretary of State",
           str_detect(file, "state_house.csv$") ~ "State Representative",
           str_detect(file, "treasurer.csv$") ~ "State Treasurer",
           str_detect(file, "turnout.csv$") ~ "Turnout",
           str_detect(file, "us_house.csv$") ~ "U.S. House",
           str_detect(file, "us_senate.csv$") ~ "U.S. Senate"
         ),
         candidate = case_when(
           str_detect(candidate, " - ") ~ candidate,
           TRUE ~ paste0(" - ", candidate)
         )) %>%
  separate(candidate, into = c("party", "candidate"), sep = " - ") %>%
  filter(!(candidate %in% c("Total Votes Cast", "Contest Total"))) %>%
  mutate(office = case_when(
    candidate == "Total" & party == "Registered Voters" ~ "Registered Voters",
    candidate == "Total" & party == "Ballots Cast" ~ "Ballots Cast",
    TRUE ~ office
         ),
         candidate = case_when(
           candidate == "Write-in Totals" ~ "Write-ins",
           candidate == "Undervotes" ~ "Under Votes",
           candidate == "Overvotes" ~ "Over Votes",
           candidate == "Total" & party == "Registered Voters" ~ NA_character_,
           candidate == "Total" & party == "Ballots Cast" ~ NA_character_,
           TRUE ~ candidate
         ),
         party = case_when(
           party %in% c("", "Registered Voters", "Ballots Cast") ~ NA_character_,
           office == "Turnout" ~ NA_character_,
           TRUE ~ party
         )) %>%
  filter(office != "Turnout") %>%
  transmute(county = "Columbia",
            precinct = Precinct,
            office,
            district = case_when(
              office == "U.S. House" ~ 1,
              office == "State Representative" ~ 31
            ),
            party,
            candidate,
            votes = str_remove_all(votes, "[^\\d]") %>% as.numeric())

write_csv(columbia, here('2020', '20201103__or__general__columbia__precinct.csv'))
