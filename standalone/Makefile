CXX = g++
CC = gcc
CXXFLAGS = -O3 -std=c++17 -Wall -Wextra -Werror
all: build/meshcore-epfl-rx
build/kiss_fft.o: ../lib/kiss_fft.c ../lib/kiss_fft.h ../lib/_kiss_fft_guts.h
	mkdir -p build
	$(CC) -O3 -I../lib -c $< -o $@
build/meshcore-epfl-rx: receiver.cpp build/kiss_fft.o
	$(CXX) $(CXXFLAGS) -I../lib receiver.cpp build/kiss_fft.o -o $@
check: all
	python3 test_receiver.py
	python3 test_weak_signal.py
clean:
	rm -rf build
.PHONY: all check clean
